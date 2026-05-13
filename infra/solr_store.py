"""SolrStore: thin Solr write adapter.

Wraps a pysolr.Solr instance for all indexing write operations
(index and delete). Search reads stay in search/fulltext.py via services.solr.
"""
from __future__ import annotations

import logging
from typing import Any

import pysolr

logger = logging.getLogger(__name__)


class SolrStore:
    """Write adapter for Solr — index, update and delete documents."""

    def __init__(self, client: pysolr.Solr) -> None:
        self.client = client

    def index_doc(self, doc: dict[str, Any]) -> None:
        """Index a single document and commit immediately."""
        self.client.add([doc], commit=True)

    def index_docs(self, docs: list[dict[str, Any]]) -> None:
        """Index a batch of documents and commit once."""
        if docs:
            self.client.add(docs, commit=True)

    def delete_by_id(self, doc_id: str) -> None:
        """Delete a document by its Solr id field and commit."""
        self.client.delete(id=doc_id, commit=True)

    def delete_by_query(self, query: str) -> None:
        """Delete all documents matching a Solr query string and commit."""
        self.client.delete(q=query, commit=True)
