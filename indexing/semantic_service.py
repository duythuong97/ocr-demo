from __future__ import annotations

import uuid
import re
from pathlib import Path
from typing import Any

try:
    from qdrant_client import QdrantClient, models
except ImportError:  # pragma: no cover - optional dependency at runtime
    QdrantClient = None
    models = None

from indexing.chunkers import (
    SemanticChunk,
    chunk_for_semantic,
)  # noqa: F401 re-exported

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


class SemanticSearchService:
    def __init__(
        self,
        storage_path: Path,
        model_name: str = "BAAI/bge-m3",
        collection_name: str = "semantic_chunks",
    ):
        if QdrantClient is None or models is None:
            raise RuntimeError(
                "qdrant-client is not installed. Install dependencies from requirements.txt"
            )

        self.storage_path = storage_path
        self.model_name = model_name
        self.collection_name = collection_name
        self._model = None
        self._vector_dim: int | None = None

        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.client = QdrantClient(path=str(self.storage_path))

    # ── Embedding ────────────────────────────────────────────────────────────

    def _load_model(self):
        if self._model is not None:
            return self._model
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.model_name)
        return self._model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._load_model().encode(texts, normalize_embeddings=True)
        return vectors.tolist() if hasattr(vectors, "tolist") else vectors

    # ── Qdrant helpers ───────────────────────────────────────────────────────

    def _ensure_collection(self, dim: int) -> None:
        if self._vector_dim is None:
            self._vector_dim = dim
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection_name in existing:
            return
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(
                size=dim, distance=models.Distance.COSINE
            ),
        )

    def clear_collection(self) -> None:
        """Drop the Qdrant collection, wiping all vectors."""
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection_name in existing:
            self.client.delete_collection(self.collection_name)
        self._vector_dim = None
        self._model = None

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

    # ── Write ────────────────────────────────────────────────────────────────

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

        vectors = self._embed([c.text for c in chunks])
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

    # ── Read ─────────────────────────────────────────────────────────────────

    def _embed_query(self, query: str) -> list[float]:
        q = query.strip()
        # BGE models generally retrieve better with retrieval instruction on query side.
        if self.model_name.lower().startswith("baai/bge-"):
            q = f"Represent this sentence for searching relevant passages: {q}"
        return self._embed([q])[0]

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

    def search(
        self,
        query: str,
        rows: int,
        repositories: list[str] | None = None,
        file_types: list[str] | None = None,
        min_score: float | None = None,
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

        query_vector = self._embed_query(query)
        flt = models.Filter(must=must_filters) if must_filters else None

        scored = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            query_filter=flt,
            limit=max(rows * 6, 24),
            with_payload=True,
        ).points

        if min_score is not None:
            scored = [p for p in scored if float(p.score) >= float(min_score)]

        if self._has_sql_intent(query):
            terms = self._extract_query_terms(query)
            focus_terms = [t for t in terms if t not in SQL_HINT_TERMS and len(t) >= 4]
            identifier_terms = self._extract_identifier_terms(query)
            sql_query_terms = [t for t in terms if t in SQL_HINT_TERMS]

            scored_with_hits: list[tuple[int, int, Any]] = []
            for point in scored:
                payload = point.payload or {}
                total_hits = self._keyword_hit_count(payload, terms)
                focus_hits = (
                    self._keyword_hit_count(payload, focus_terms) if focus_terms else 0
                )
                scored_with_hits.append((focus_hits, total_hits, point))

            if identifier_terms:
                prioritized = []
                for _, _, p in scored_with_hits:
                    payload = p.payload or {}
                    id_hits = self._keyword_hit_count(payload, identifier_terms)
                    sql_hits = self._keyword_hit_count(payload, sql_query_terms)
                    if id_hits > 0 and sql_hits > 0:
                        prioritized.append(p)

                if not prioritized:
                    prioritized = [
                        p
                        for _, _, p in scored_with_hits
                        if self._keyword_hit_count(p.payload or {}, identifier_terms)
                        > 0
                    ]
            elif focus_terms:
                prioritized = [p for fh, _, p in scored_with_hits if fh > 0]
            else:
                prioritized = [p for _, th, p in scored_with_hits if th > 0]

            if prioritized:
                scored = prioritized

            def sql_rank_key(point: Any) -> tuple[int, int, float]:
                payload = point.payload or {}
                total_hits = self._keyword_hit_count(payload, terms)
                focus_hits = (
                    self._keyword_hit_count(payload, focus_terms) if focus_terms else 0
                )
                return (focus_hits, total_hits, float(point.score))

            scored = sorted(scored, key=sql_rank_key, reverse=True)

        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for point in scored:
            payload = point.payload or {}
            fp = str(payload.get("file_path", ""))
            if not fp:
                continue
            chunk_id = str(payload.get("chunk_id", ""))
            dedup_key = f"{fp}::{chunk_id}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            out.append(
                {
                    "file_path": fp,
                    "rel_path": str(payload.get("rel_path", "")),
                    "chunk_id": chunk_id,
                    "chunk_type": str(payload.get("chunk_type", "text")),
                    "text": str(payload.get("text", "")),
                    "score": float(point.score),
                    "repository": str(payload.get("repository", "")),
                    "file_type": str(payload.get("file_type", "")),
                    "repository_url_base": str(payload.get("repository_url_base", "")),
                }
            )
            if len(out) >= rows:
                break
        return out
