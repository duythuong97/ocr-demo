"""Backward-compat re-export. The canonical implementation is infra/qdrant_store.py."""
from infra.qdrant_store import QdrantStore as IngestService

__all__ = ["IngestService"]
