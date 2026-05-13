"""LLM listwise reranker (RankGPT pattern) for the retrieval pipeline.

Uses the Ollama/OpenAI-compatible endpoint configured via RERANKER_URL / RERANKER_MODEL
(both default to the main LLM endpoint when not set).

Public API:
    get_reranker() -> BaseReranker
    reranker.rerank(query, docs, top_k) -> list[dict]
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

import config as cfg

logger = logging.getLogger(__name__)


# ── Abstract base ─────────────────────────────────────────────────────────────

class BaseReranker(ABC):
    @abstractmethod
    def rerank(
        self,
        query: str,
        docs: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        ...


# ── LLM listwise reranker (RankGPT pattern) ──────────────────────────────────

_LISTWISE_SYSTEM = """\
You are a relevance ranking assistant. Your job is to reorder a list of documents \
by how relevant they are to the given query.
Return ONLY a comma-separated list of document numbers in order from most to least relevant.
Example output format: 3,1,5,2,4
Output nothing else — no explanation, no punctuation other than commas."""

_LISTWISE_USER_TMPL = """\
Query: {query}

Documents:
{doc_list}

Rank the documents from most to least relevant to the query.
Return only the numbers, comma-separated."""


class LlmReranker(BaseReranker):
    """Listwise LLM reranker using any OpenAI-compatible API (Ollama, LM Studio, …).

    Sends one chat request with all candidate docs and parses the
    returned ranking order. For batches larger than *batch_size*, splits
    into windows and merges by tournament selection.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        batch_size: int = 20,
        timeout: int = 60,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.batch_size = batch_size
        self.timeout = timeout

    def rerank(self, query: str, docs: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
        if not docs:
            return docs
        if len(docs) == 1:
            return docs[:top_k]

        try:
            ranked = self._rank_window(query, docs, list(range(len(docs))))
            reranked = [docs[i] for i in ranked]
            for rank, doc in enumerate(reranked):
                # Normalise score to 1.0 → 0.0 descending
                doc["score"] = max(0.0, 1.0 - rank / len(reranked))
            return reranked[:top_k]
        except Exception as exc:
            logger.warning("LlmReranker failed, falling back to input order: %s", exc)
            return docs[:top_k]

    def _rank_window(
        self,
        query: str,
        docs: list[dict[str, Any]],
        indices: list[int],
    ) -> list[int]:
        """Rank a window of doc indices. Splits into batches if needed."""
        if len(indices) <= self.batch_size:
            return self._single_pass(query, docs, indices)

        # Split into overlapping windows, merge by position average
        mid = len(indices) // 2
        left = self._rank_window(query, docs, indices[:mid + 2])
        right = self._rank_window(query, docs, indices[mid:])
        # Simple merge: left takes priority for top half
        merged: list[int] = []
        seen: set[int] = set()
        for idx in left + right:
            if idx not in seen:
                merged.append(idx)
                seen.add(idx)
        return merged

    def _single_pass(
        self,
        query: str,
        docs: list[dict[str, Any]],
        indices: list[int],
    ) -> list[int]:
        """One LLM call to rank a subset of docs. Returns ordered indices."""
        doc_list_parts = []
        for pos, idx in enumerate(indices, start=1):
            snippet = docs[idx].get("text", "")[:400].replace("\n", " ").strip()
            fname = docs[idx].get("rel_path", "") or docs[idx].get("file_path", "")
            doc_list_parts.append(f"[{pos}] ({fname})\n{snippet}")

        doc_list = "\n\n".join(doc_list_parts)
        prompt = _LISTWISE_USER_TMPL.format(query=query, doc_list=doc_list)

        import requests
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": _LISTWISE_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 128,
                "stream": False,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        logger.debug("LlmReranker raw response: %r", content)

        # Parse: expect "3,1,2" or "3, 1, 2" — positions are 1-based
        raw_numbers = re.findall(r"\d+", content)
        ranked_indices: list[int] = []
        seen_positions: set[int] = set()
        for num_str in raw_numbers:
            pos = int(num_str)  # 1-based position in the window
            if 1 <= pos <= len(indices) and pos not in seen_positions:
                seen_positions.add(pos)
                ranked_indices.append(indices[pos - 1])

        # Append any indices not mentioned by the LLM (safety net)
        for idx in indices:
            if idx not in ranked_indices:
                ranked_indices.append(idx)

        return ranked_indices


# ── Singleton factory ─────────────────────────────────────────────────────────

_reranker_instance: BaseReranker | None = None


def get_reranker() -> BaseReranker:
    """Return the LLM reranker singleton (lazy-initialised)."""
    global _reranker_instance

    if _reranker_instance is not None:
        return _reranker_instance

    url = cfg.RERANKER_URL
    model = cfg.RERANKER_MODEL
    batch = cfg.RERANKER_LISTWISE_BATCH
    logger.info("Reranker: LLM listwise — url=%s model=%s batch=%d", url, model, batch)
    _reranker_instance = LlmReranker(base_url=url, model=model, batch_size=batch)
    return _reranker_instance
