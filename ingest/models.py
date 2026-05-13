"""Ingest data models (dataclasses)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IndexJobConfig:
    root_path: str
    repository: str
    repository_path: str
    repository_url_base: str
    extensions: list[str]
    index_mode: str = "both"  # "both" | "solr" | "semantic"
