"""Backward-compat re-export. The canonical implementation is infra/qdrant_store.py."""
from infra.qdrant_store import QdrantStore as RetrievalService, SQL_HINT_TERMS

__all__ = ["RetrievalService", "SQL_HINT_TERMS"]
