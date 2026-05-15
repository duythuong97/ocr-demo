"""QdrantStore: unified Qdrant adapter for both write (ingest) and read (retrieval).

Single QdrantClient instance shared across the application:
  - Write path: upsert_file, delete_file, clear_collection
  - Read path:  search (vector + fulltext + RRF merge + rerank)
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any

try:
    from qdrant_client import QdrantClient, models
except ImportError:  # pragma: no cover
    QdrantClient = None  # type: ignore[misc,assignment]
    models = None  # type: ignore[assignment]

import config as cfg
from infra.embedding import EmbedderProtocol
from ingest.chunkers import chunk_for_semantic
from retrieval.reranker import get_reranker

logger = logging.getLogger(__name__)

# Exported for backward compat (search/result.py imports this)
SQL_HINT_TERMS = {
    "select",
    "insert",
    "update",
    "delete",
    "merge",
    "into",
    "where",
    "join",
    "values",
}


class QdrantStore:
    """Unified Qdrant adapter: handles chunk/embed/upsert (write) and vector/text search (read)."""

    def __init__(
        self,
        qdrant_url: str,
        collection_name: str,
        embedder: EmbedderProtocol,
    ) -> None:
        if QdrantClient is None or models is None:
            raise RuntimeError(
                "qdrant-client is not installed. Install dependencies from requirements.txt"
            )
        self.qdrant_url = qdrant_url
        self.collection_name = collection_name
        self.embedder = embedder
        self._vector_dim: int | None = None
        self.client = QdrantClient(url=qdrant_url)

    def get_config(self) -> dict:
        return self.embedder.get_config()

    # ── Collection management ──────────────────────────────────────────────────

    def _ensure_collection(self, dim: int) -> None:
        if self._vector_dim is None:
            self._vector_dim = dim
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection_name not in existing:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=dim, distance=models.Distance.COSINE
                ),
            )
        # Ensure fulltext payload index on "text" field (idempotent)
        try:
            self.client.create_payload_index(
                collection_name=self.collection_name,
                field_name="text",
                field_schema=models.TextIndexParams(
                    type="text",
                    tokenizer="multilingual",
                    min_token_len=1,
                    max_token_len=20,
                    lowercase=True,
                ),
            )
        except Exception as exc:
            logger.debug(
                "Qdrant payload index creation skipped (already exists or older client): %s", exc
            )

    def clear_collection(self) -> None:
        """Drop the Qdrant collection, wiping all vectors."""
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection_name in existing:
            self.client.delete_collection(self.collection_name)
        self._vector_dim = None

    # ── Write ──────────────────────────────────────────────────────────────────

    def upsert_file(
        self,
        file_path: str,
        text: str,
        repository: str,
        file_type: str,
        rel_path: str = "",
        repository_url_base: str = "",
        max_chunks: int | None = None,
    ) -> dict[str, Any]:
        chunks = chunk_for_semantic(file_path, text)
        original_chunk_count = len(chunks)

        truncated_chunks = False
        if max_chunks and max_chunks > 0 and len(chunks) > max_chunks:
            chunks = chunks[:max_chunks]
            truncated_chunks = True

        if not chunks:
            return {
                "chunks": 0,
                "original_chunks": original_chunk_count,
                "truncated_chunks": truncated_chunks,
                "vector_dim": 0,
            }

        vectors = self.embedder.embed([c.text for c in chunks])
        if not vectors:
            return {
                "chunks": 0,
                "original_chunks": original_chunk_count,
                "truncated_chunks": truncated_chunks,
                "vector_dim": 0,
            }

        self._ensure_collection(dim=len(vectors[0]))

        # Remove old points for this file before upserting fresh ones
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="file_path",
                            match=models.MatchValue(value=file_path),
                        )
                    ]
                )
            ),
        )

        points: list[Any] = [
            models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{file_path}:{chunk.chunk_id}")),
                vector=vectors[idx],
                payload={
                    "file_path": file_path,
                    "rel_path": rel_path,
                    "chunk_id": chunk.chunk_id,
                    "chunk_type": chunk.chunk_type,
                    "text": chunk.text,
                    "repository": repository,
                    "file_type": file_type,
                    "repository_url_base": repository_url_base,
                    "heading": chunk.heading,
                    "sheet_name": chunk.sheet_name,
                    "page_num": chunk.page_num,
                    "chunk_index": idx,
                },
            )
            for idx, chunk in enumerate(chunks)
        ]
        self.client.upsert(collection_name=self.collection_name, points=points)
        return {
            "chunks": len(chunks),
            "original_chunks": original_chunk_count,
            "truncated_chunks": truncated_chunks,
            "vector_dim": len(vectors[0]),
        }

    def delete_file(self, file_path: str) -> None:
        """Remove all Qdrant points associated with a specific file."""
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection_name not in existing:
            return
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="file_path",
                            match=models.MatchValue(value=file_path),
                        )
                    ]
                )
            ),
        )

    # ── Read / Search ──────────────────────────────────────────────────────────

    def _embed_query(self, query: str) -> list[float]:
        return self.embedder.embed([query.strip()])[0]

    def _has_sql_intent(self, query: str) -> bool:
        low = query.lower()
        return any(term in low for term in SQL_HINT_TERMS)

    def _extract_query_terms(self, query: str) -> list[str]:
        return [
            t
            for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query.lower())
            if t not in {"ch", "cho", "nao", "where", "the", "and", "or"}
        ]

    def _extract_identifier_terms(self, query: str) -> list[str]:
        return [t.lower() for t in re.findall(r"\b[A-Z_][A-Z0-9_]{2,}\b", query)]

    def _keyword_hit_count(self, payload: dict[str, Any], terms: list[str]) -> int:
        text = str(payload.get("text", "")).lower()
        rel_path = str(payload.get("rel_path", "")).lower()
        chunk_type = str(payload.get("chunk_type", "")).lower()
        haystack = f"{text}\n{rel_path}\n{chunk_type}"
        return sum(1 for t in terms if t in haystack)

    def _fulltext_search(
        self,
        query: str,
        rows: int,
        repositories: list[str] | None = None,
        file_types: list[str] | None = None,
    ) -> list[Any]:
        """Qdrant scroll with MatchText fulltext filter."""
        must: list[Any] = []
        if repositories:
            must.append(
                models.FieldCondition(key="repository", match=models.MatchAny(any=repositories))
            )
        if file_types:
            must.append(
                models.FieldCondition(key="file_type", match=models.MatchAny(any=file_types))
            )
        must.append(
            models.FieldCondition(key="text", match=models.MatchText(text=query))
        )
        points, _ = self.client.scroll(
            collection_name=self.collection_name,
            scroll_filter=models.Filter(must=must),
            limit=max(rows * 4, 16),
            with_payload=True,
            with_vectors=False,
        )
        return list(points)

    def _points_to_dicts(self, points: list[Any]) -> list[dict[str, Any]]:
        """Convert Qdrant ScoredPoint/Record objects to normalized dicts."""
        result = []
        for point in points:
            payload = point.payload or {}
            fp = str(payload.get("file_path", ""))
            if not fp:
                continue
            result.append({
                "_id": str(point.id),
                "file_path": fp,
                "rel_path": str(payload.get("rel_path", "")),
                "chunk_id": str(payload.get("chunk_id", "")),
                "chunk_type": str(payload.get("chunk_type", "text")),
                "text": str(payload.get("text", "")),
                "score": float(getattr(point, "score", 0.0)),
                "repository": str(payload.get("repository", "")),
                "file_type": str(payload.get("file_type", "")),
                "repository_url_base": str(payload.get("repository_url_base", "")),
            })
        return result

    @staticmethod
    def _rrf_merge(
        vector_docs: list[dict[str, Any]],
        text_docs: list[dict[str, Any]],
        k: int = 60,
    ) -> list[dict[str, Any]]:
        """Reciprocal Rank Fusion: merge vector and fulltext result lists."""
        rrf_scores: dict[str, float] = {}
        id_to_doc: dict[str, dict[str, Any]] = {}
        for rank, doc in enumerate(vector_docs):
            pid = doc["_id"]
            rrf_scores[pid] = rrf_scores.get(pid, 0.0) + 1.0 / (k + rank + 1)
            id_to_doc[pid] = doc
        for rank, doc in enumerate(text_docs):
            pid = doc["_id"]
            rrf_scores[pid] = rrf_scores.get(pid, 0.0) + 1.0 / (k + rank + 1)
            id_to_doc[pid] = doc
        merged = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        result = []
        for pid, score in merged:
            d = dict(id_to_doc[pid])
            d["score"] = score
            result.append(d)
        return result

    def _rerank(
        self,
        query: str,
        docs: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        return get_reranker().rerank(query, docs, top_k)

    def search(
        self,
        query: str,
        rows: int,
        repositories: list[str] | None = None,
        file_types: list[str] | None = None,
        min_score: float | None = None,
        hyde_text: str | None = None,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []

        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection_name not in existing:
            return []

        must_filters: list[Any] = []
        if repositories:
            must_filters.append(
                models.FieldCondition(
                    key="repository", match=models.MatchAny(any=repositories)
                )
            )
        if file_types:
            must_filters.append(
                models.FieldCondition(
                    key="file_type", match=models.MatchAny(any=file_types)
                )
            )

        embed_text = hyde_text if hyde_text else query
        query_vector = self._embed_query(embed_text)
        flt = models.Filter(must=must_filters) if must_filters else None

        vector_points = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            query_filter=flt,
            limit=max(rows * 6, 24),
            with_payload=True,
        ).points

        if min_score is not None:
            vector_points = [p for p in vector_points if float(p.score) >= float(min_score)]

        try:
            text_points = self._fulltext_search(query, rows, repositories, file_types)
        except Exception as exc:
            logger.debug("Fulltext search failed: %s", exc)
            text_points = []

        vector_docs = self._points_to_dicts(vector_points)
        text_docs = self._points_to_dicts(text_points)
        candidates = self._rrf_merge(vector_docs, text_docs, k=cfg.RETRIEVAL_RRF_K)

        # SQL-intent keyword boost
        if self._has_sql_intent(query):
            terms = self._extract_query_terms(query)
            focus_terms = [t for t in terms if t not in SQL_HINT_TERMS and len(t) >= 4]
            identifier_terms = self._extract_identifier_terms(query)
            sql_query_terms = [t for t in terms if t in SQL_HINT_TERMS]

            if identifier_terms:
                prioritized = [
                    d for d in candidates
                    if self._keyword_hit_count(d, identifier_terms) > 0
                    and self._keyword_hit_count(d, sql_query_terms) > 0
                ]
                if not prioritized:
                    prioritized = [
                        d for d in candidates
                        if self._keyword_hit_count(d, identifier_terms) > 0
                    ]
            elif focus_terms:
                prioritized = [d for d in candidates if self._keyword_hit_count(d, focus_terms) > 0]
            else:
                prioritized = [d for d in candidates if self._keyword_hit_count(d, terms) > 0]

            if prioritized:
                candidates = prioritized

            def sql_rank_key(doc: dict[str, Any]) -> tuple[int, int, float]:
                total_hits = self._keyword_hit_count(doc, terms)
                focus_hits = self._keyword_hit_count(doc, focus_terms) if focus_terms else 0
                return (focus_hits, total_hits, doc.get("score", 0.0))

            candidates = sorted(candidates, key=sql_rank_key, reverse=True)

        # Dedup by file_path + chunk_id
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for doc in candidates:
            dedup_key = f"{doc['file_path']}::{doc['chunk_id']}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            deduped.append(doc)

        return self._rerank(query, deduped, rows)
