"""Agentic RAG: tool-calling loop + streaming final answer."""

from __future__ import annotations

import json
import logging
from typing import Generator

import config as cfg
from retrieval.proxy_chat import LLMClient, LLMError
from retrieval.tools import TOOLS, execute_tool

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────────────

_AGENT_SYSTEM = """\
You are a helpful assistant with access to a codebase knowledge base.

When answering questions about code, architecture, APIs, or any technical topic:
1. Use the provided tools to retrieve relevant information first.
2. You may call multiple tools in sequence (e.g. search then explore graph).
3. Base your final answer ONLY on the retrieved information.
4. If no relevant information is found after searching, say so clearly.

Tool selection rules — follow these strictly:

Use get_graph_neighbors for questions about RELATIONSHIPS:
  - Which functions/services READ or WRITE a specific table?
  - Who calls this function? What does this function call?
  - What tables/APIs does this service use?
  - Impact analysis, call chains, data-flow dependencies.
  → Pass the bare entity name (e.g. 'AUDIT_LOG', 'EMPLOYEES', 'UserService').

Use search_documents for questions about CONTENT or CONCEPTS:
  - How is X implemented? What does this code do?
  - Find code related to a topic or feature.
  → Use a natural-language query string.
"""

_ANSWER_SYSTEM = cfg.RAG_SYSTEM_PROMPT or """\
You are a helpful assistant. Answer the user's question using ONLY the context documents provided below.
If the context does not contain enough information, say so — do not invent details.
"""


def _build_context_text(docs: list[dict]) -> str:
    parts = []
    for idx, d in enumerate(docs, 1):
        fname = d.get("file") or d.get("file_path", f"doc{idx}")
        text = str(d.get("text", ""))[:cfg.RAG_CONTEXT_CHARS_PER_DOC]
        parts.append(f"[{idx}] {fname}\n{text}")
    return "\n\n".join(parts)


def _build_final_messages(
    history: list[dict],
    user_message: str,
    docs: list[dict],
) -> list[dict]:
    """Build the messages list for the final streaming answer with context injected."""
    ctx = _build_context_text(docs)
    system = _ANSWER_SYSTEM
    if ctx:
        system += f"\n\n---\nContext documents:\n{ctx}\n---"
    else:
        system += (
            "\n\n---\nNo relevant documents were found in the knowledge base for this query. "
            "Inform the user clearly.\n---"
        )
    messages: list[dict] = [{"role": "system", "content": system}]
    for turn in history:
        if isinstance(turn, dict) and turn.get("role") in ("user", "assistant"):
            messages.append(
                {"role": turn["role"], "content": str(turn.get("content", ""))}
            )
    messages.append({"role": "user", "content": user_message})
    return messages


# ── Agentic streaming loop ────────────────────────────────────────────────────


def stream_rag_response(
    user_message: str,
    history: list[dict],
    top_k: int,
) -> Generator[str, None, None]:
    """Run the agentic RAG loop and yield SSE-formatted strings.

    SSE event types:
      tool_call    — LLM decided to call a tool  {name, args, id}
      tool_result  — tool execution finished      {id, name, count, error?}
      context      — accumulated docs for UI      {docs, graph}
      token        — final answer text chunk      {text}
      error        — fatal error                  {text}
    """
    client = LLMClient(base_url=cfg.LLM_BASE_URL, timeout=cfg.LLM_TIMEOUT)
    max_iter = max(1, cfg.AGENT_MAX_ITERATIONS)

    # Build initial messages for the tool-calling phase
    agent_messages: list[dict] = [{"role": "system", "content": _AGENT_SYSTEM}]
    for turn in history:
        if isinstance(turn, dict) and turn.get("role") in ("user", "assistant"):
            agent_messages.append(
                {"role": turn["role"], "content": str(turn.get("content", ""))}
            )
    agent_messages.append({"role": "user", "content": user_message})

    # ── Multi-turn query rewriting ────────────────────────────────────────────
    # When there is prior history, the user's message may use pronouns or
    # elliptical references ("tell me more", "what about X?") that won't
    # embed well in isolation.  Rewrite it as a standalone question first.
    search_query = user_message  # used for auto-search fallback + HyDE
    if history:
        try:
            _rw_messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a query rewriter. Given the conversation history and the latest user message, "
                        "rewrite the LATEST user message as a single self-contained search query "
                        "(one sentence, no pronouns/ellipsis). "
                        "Output ONLY the rewritten query — no explanation, no quotes."
                    ),
                }
            ]
            for turn in history[-6:]:
                if isinstance(turn, dict) and turn.get("role") in ("user", "assistant"):
                    _rw_messages.append(
                        {"role": turn["role"], "content": str(turn.get("content", ""))[:400]}
                    )
            _rw_messages.append({"role": "user", "content": user_message})
            _rw_resp = client.chat_complete(
                messages=_rw_messages, model=cfg.LLM_MODEL, max_tokens=80
            )
            _choices = (_rw_resp.get("choices") or [])
            _rewritten = (_choices[0].get("message", {}).get("content", "") if _choices else "").strip()
            if _rewritten and len(_rewritten) > 4:
                search_query = _rewritten
                logger.info("agent_rag: query rewritten to %r", search_query[:120])
        except Exception as _exc:
            logger.debug("agent_rag: query rewriting failed: %s", _exc)
    accumulated_docs: list[dict] = []   # collected retrieval results across all tool calls
    accumulated_graph: dict = {}        # merged vis-network {nodes, edges} for UI
    used_tools = False
    _called_signatures: set[str] = set()   # dedup: (tool_name, args_json)

    logger.info(
        "agent_rag: start user=%r top_k=%d max_iter=%d model=%r",
        user_message[:80],
        top_k,
        max_iter,
        cfg.LLM_MODEL,
    )

    # ── Phase 1: Tool-calling loop ────────────────────────────────────────────
    for iteration in range(max_iter):
        logger.info(
            "agent_rag: iteration %d/%d msgs=%d",
            iteration + 1,
            max_iter,
            len(agent_messages),
        )

        # Force at least one tool call on the first iteration unless the
        # message is a clear non-technical greeting/chitchat.
        # Subsequent iterations always use "auto" so the LLM can stop searching.
        _is_greeting = (
            iteration == 0
            and len(user_message.strip()) < 40
            and not any(
                kw in user_message.lower()
                for kw in ("search", "find", "what", "how", "why", "where", "who",
                           "list", "show", "tell", "explain", "get", "give",
                           # Japanese particles/verbs that suggest a question
                           "は", "が", "を", "に", "で", "の", "か", "教", "見", "調")
            )
        )
        tool_choice = "auto" if _is_greeting else ("required" if iteration == 0 else "auto")

        # ── Stream the tool-calling phase so thinking text trickles in real-time
        msg: dict | None = None
        finish_reason = "stop"
        try:
            for stream_evt in client.chat_stream_with_tools(
                messages=agent_messages,
                model=cfg.LLM_MODEL,
                tools=TOOLS,
                tool_choice=tool_choice,
            ):
                kind = stream_evt[0]
                if kind == "content_token":
                    yield _sse("thinking_token", {"text": stream_evt[1]})
                elif kind in ("tool_calls_done", "stop"):
                    msg = stream_evt[1]
                    finish_reason = "tool_calls" if kind == "tool_calls_done" else "stop"
                elif kind == "error":
                    raise stream_evt[1]
        except LLMError as exc:
            err_msg = str(exc)
            logger.warning("agent_rag LLM error (tool phase): %s", err_msg)
            # Some local models fail when tools/tool_choice are sent:
            # - HTTP 400 (KV-cache / quantisation issues)
            # - HTTP 422 (schema validation rejects the tools field)
            # - "NotImplementedError" / "NYI" / "not supported" in the message
            # In all these cases fall through to the auto-search fallback
            # instead of surfacing a raw error to the user.
            _no_tool_support = (
                "400" in err_msg
                or "422" in err_msg
                or "NotImplementedError" in err_msg
                or "NYI" in err_msg
                or "not supported" in err_msg.lower()
                or "tool" in err_msg.lower()
            )
            if _no_tool_support:
                logger.warning(
                    "agent_rag: model does not support tool_choice=%r, switching to auto-search fallback",
                    tool_choice,
                )
                break
            yield _sse("error", {"text": err_msg})
            yield "data: [DONE]\n\n"
            return

        if msg is None:
            break

        if finish_reason == "tool_calls" or msg.get("tool_calls"):
            tool_calls: list[dict] = msg.get("tool_calls") or []
            agent_messages.append(msg)
            used_tools = True
            new_calls = 0

            for tc in tool_calls:
                fn = tc.get("function", {})
                fn_name = fn.get("name", "")
                fn_args = fn.get("arguments", "{}")
                tc_id = tc.get("id") or f"call_{iteration}_{fn_name}"

                # ── Dedup: skip if same tool+args already called ──────────────
                # Normalise the args JSON so key-order and optional defaults
                # (e.g. top_k added/omitted) don't defeat deduplication.
                try:
                    _norm = json.dumps(json.loads(fn_args), sort_keys=True, separators=(",", ":"))
                except Exception:
                    _norm = fn_args
                sig = f"{fn_name}::{_norm}"
                if sig in _called_signatures:
                    logger.info("agent_rag: skipping duplicate tool call %r", sig[:80])
                    # Still need a tool result message so the conversation stays valid
                    agent_messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": json.dumps({"note": "duplicate call skipped"}, default=str),
                    })
                    continue
                _called_signatures.add(sig)
                new_calls += 1

                yield _sse("tool_call", {"id": tc_id, "name": fn_name, "args": fn_args})
                try:
                    result, docs, graph = execute_tool(fn_name, fn_args)
                    accumulated_docs.extend(docs)
                except Exception as _tool_exc:
                    logger.error(
                        "agent_rag: tool %r raised unexpectedly: %s",
                        fn_name, _tool_exc, exc_info=True,
                    )
                    result = {"error": str(_tool_exc)}
                    docs = []
                    graph = {}

                # Merge graph nodes/edges (may be called multiple times)
                if graph:
                    accumulated_graph.setdefault("nodes", []).extend(graph.get("nodes", []))
                    accumulated_graph.setdefault("edges", []).extend(graph.get("edges", []))

                logger.info(
                    "agent_rag: tool=%r docs=%d error=%s",
                    fn_name,
                    len(docs),
                    result.get("error"),
                )
                yield _sse(
                    "tool_result",
                    {
                        "id": tc_id,
                        "name": fn_name,
                        "count": len(docs),
                        "error": result.get("error"),
                    },
                )

                agent_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": json.dumps(result, default=str),
                    }
                )

            # Stop looping if we already have enough docs or made no new calls.
            # Use > 0 (not >= top_k) so a partial result doesn't re-trigger the
            # same search; multi-hop graph calls can still happen on first iteration.
            if len(accumulated_docs) > 0 or new_calls == 0:
                logger.info(
                    "agent_rag: stopping tool loop — docs=%d new_calls=%d",
                    len(accumulated_docs), new_calls,
                )
                break
            continue  # let LLM decide next step

        # finish_reason is "stop" — done with tool loop
        break

    # ── Phase 2: Fallback auto-search if model didn't use tools ──────────────
    if not used_tools:
        logger.warning(
            "agent_rag: model did not call any tools — running auto search_documents"
        )
        auto_args = json.dumps({"query": search_query, "top_k": top_k})
        yield _sse(
            "tool_call", {"id": "auto_0", "name": "search_documents", "args": auto_args}
        )
        result, docs, _g = execute_tool("search_documents", auto_args)
        accumulated_docs.extend(docs)
        yield _sse(
            "tool_result",
            {
                "id": "auto_0",
                "name": "search_documents",
                "count": len(docs),
                "error": result.get("error"),
            },
        )

    # ── Phase 3: Emit context and stream final answer ─────────────────────────
    ctx_payload = [
        {
            "file_path": d.get("file_path", ""),
            "rel_path": d.get("file", "") or d.get("file_path", ""),
            "file": d.get("file", ""),
            "text": str(d.get("text", ""))[:2000],
            "score": d.get("score"),
        }
        for d in accumulated_docs
    ]
    yield _sse("context", {"docs": ctx_payload, "graph": accumulated_graph})

    # Build a fresh prompt with context injected — always, regardless of tool support
    final_messages = _build_final_messages(history, user_message, accumulated_docs)

    logger.info(
        "agent_rag: final stream model=%r msgs=%d docs=%d",
        cfg.LLM_MODEL,
        len(final_messages),
        len(accumulated_docs),
    )

    chunk_count = 0
    try:
        for chunk in client.chat_stream(messages=final_messages, model=cfg.LLM_MODEL):
            chunk_count += 1
            yield _sse("token", {"text": chunk})
        logger.info("agent_rag: done chunks=%d", chunk_count)
    except LLMError as exc:
        logger.error("agent_rag streaming error: %s", exc)
        yield _sse("error", {"text": str(exc)})

    yield "data: [DONE]\n\n"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _sse(event_type: str, data: dict) -> str:
    data["type"] = event_type
    return f"data: {json.dumps(data, default=str)}\n\n"
