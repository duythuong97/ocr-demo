"""OpenAI-compatible LLM client.

Supports any endpoint that speaks the OpenAI Chat Completions API
(LMStudio, Ollama, vLLM, OpenAI, Azure, …).

Base URL example: http://localhost:1234/v1
Endpoints used:
  POST {base_url}/chat/completions          — streaming + non-streaming
  GET  {base_url}/models                    — availability check
"""

from __future__ import annotations

import json
import logging
from typing import Generator

import requests

logger = logging.getLogger("app")


class LLMError(Exception):
    """Raised when the LLM endpoint returns an error or is unreachable."""


class LLMClient:
    """Client for any OpenAI-compatible LLM endpoint."""

    def __init__(self, base_url: str, timeout: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ── Availability ──────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """HEAD /models — lightweight reachability check."""
        try:
            requests.get(f"{self.base_url}/models", timeout=5)
            return True
        except requests.exceptions.RequestException:
            return False

    # ── Non-streaming (tool calling / agentic loop) ───────────────────────────

    def chat_complete(
        self,
        messages: list[dict],
        model: str = "local-model",
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> dict:
        """Non-streaming chat completion. Returns the full response dict.

        Used in the agentic loop so we can inspect ``finish_reason``
        and ``tool_calls`` before deciding the next step.
        """
        payload: dict = {"model": model, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        url = f"{self.base_url}/chat/completions"
        logger.debug(
            "LLMClient.chat_complete: POST %s model=%r msgs=%d tools=%d",
            url,
            model,
            len(messages),
            len(tools or []),
        )
        return self._post(url, payload)

    # ── Streaming tool-calling phase ─────────────────────────────────────────

    def chat_stream_with_tools(
        self,
        messages: list[dict],
        model: str = "local-model",
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> Generator[tuple, None, None]:
        """Streaming chat completion that surfaces thinking tokens AND tool calls.

        Yields tuples:
          ("content_token", text)            — thinking/reasoning text chunk
          ("tool_calls_done", message_dict)  — full assistant message with tool_calls
          ("stop", message_dict)             — model finished without tool calls
          ("error", LLMError)                — network / HTTP error
        """
        payload: dict = {"model": model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        url = f"{self.base_url}/chat/completions"
        logger.debug(
            "LLMClient.chat_stream_with_tools: POST %s model=%r msgs=%d tools=%d",
            url,
            model,
            len(messages),
            len(tools or []),
        )

        accumulated_content = ""
        tool_calls_acc: dict[int, dict] = {}  # index → partial tool call
        finish_reason = "stop"

        try:
            with requests.post(
                url, json=payload, stream=True, timeout=self.timeout
            ) as res:
                res.raise_for_status()
                for raw_line in res.iter_lines():
                    if not raw_line:
                        continue
                    line = (
                        raw_line.decode("utf-8", errors="replace")
                        if isinstance(raw_line, bytes)
                        else raw_line
                    )
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        choice = chunk.get("choices", [{}])[0]
                        delta = choice.get("delta", {})
                        fr = choice.get("finish_reason")
                        if fr:
                            finish_reason = fr

                        # Thinking / reasoning content tokens
                        text = delta.get("content") or ""
                        if text:
                            accumulated_content += text
                            yield ("content_token", text)

                        # Tool call deltas — accumulate per index
                        for tc_delta in delta.get("tool_calls") or []:
                            idx = tc_delta.get("index", 0)
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            acc = tool_calls_acc[idx]
                            if tc_delta.get("id"):
                                acc["id"] = tc_delta["id"]
                            fn_d = tc_delta.get("function") or {}
                            if fn_d.get("name"):
                                acc["function"]["name"] += fn_d["name"]
                            if fn_d.get("arguments"):
                                acc["function"]["arguments"] += fn_d["arguments"]

                    except (json.JSONDecodeError, IndexError, KeyError):
                        continue

        except requests.exceptions.RequestException as exc:
            yield ("error", self._wrap(exc, url))
            return

        # Build the final message dict as if it came from chat_complete()
        message: dict = {"role": "assistant", "content": accumulated_content or None}
        if tool_calls_acc:
            message["tool_calls"] = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
            yield ("tool_calls_done", message)
        else:
            yield ("stop", message)

    # ── Streaming (final answer delivery) ────────────────────────────────────

    def chat_stream(
        self,
        messages: list[dict],
        model: str = "local-model",
    ) -> Generator[str, None, None]:
        """Streaming chat completion. Yields text chunks via SSE."""
        payload = {"model": model, "messages": messages, "stream": True}
        url = f"{self.base_url}/chat/completions"
        logger.debug(
            "LLMClient.chat_stream: POST %s model=%r msgs=%d",
            url,
            model,
            len(messages),
        )
        try:
            with requests.post(
                url, json=payload, stream=True, timeout=self.timeout
            ) as res:
                res.raise_for_status()
                for raw_line in res.iter_lines():
                    if not raw_line:
                        continue
                    line = (
                        raw_line.decode("utf-8", errors="replace")
                        if isinstance(raw_line, bytes)
                        else raw_line
                    )
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        text = delta.get("content") or ""
                        if text:
                            yield text
                    except (json.JSONDecodeError, IndexError, KeyError):
                        continue
        except requests.exceptions.RequestException as exc:
            raise self._wrap(exc, url) from exc

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _post(self, url: str, payload: dict) -> dict:
        try:
            res = requests.post(url, json=payload, timeout=self.timeout)
            res.raise_for_status()
            return res.json()
        except requests.exceptions.RequestException as exc:
            raise self._wrap(exc, url) from exc

    def _wrap(self, exc: Exception, url: str) -> LLMError:
        if isinstance(exc, requests.exceptions.ConnectionError):
            return LLMError(f"Cannot connect to LLM at {url}. Is it running?")
        if isinstance(exc, requests.exceptions.Timeout):
            return LLMError(f"LLM request timed out after {self.timeout}s.")
        if isinstance(exc, requests.exceptions.HTTPError):
            body = ""
            try:
                body = exc.response.text[:400]
            except (AttributeError, OSError):
                pass  # response body unavailable
            return LLMError(f"LLM HTTP {exc.response.status_code}: {body}")
        return LLMError(str(exc))


# Backward-compatible aliases
ProxyChatClient = LLMClient
ProxyChatError = LLMError
