"""Search package public API."""
from __future__ import annotations

from search.fulltext import execute_fulltext_search as execute_search

__all__ = ["execute_search"]
