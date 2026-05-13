"""BaseExtractor ABC — implement this to add a new language or format."""
from __future__ import annotations

from abc import ABC, abstractmethod

from graph.db.entities import ExtractionContext, ExtractionResult


class BaseExtractor(ABC):
    """Contract for all graph extractors.

    Extractors are stateless — extract() may be called concurrently.
    They NEVER write to Neo4j directly; they return ExtractionResult.
    """

    @abstractmethod
    def can_handle(self, file_path: str, text: str) -> bool:
        """Return True if this extractor should process the given file."""

    @abstractmethod
    def extract(
        self,
        file_path: str,
        text: str,
        context: ExtractionContext,
    ) -> ExtractionResult:
        """Parse file and return graph nodes + edges.  Must not raise."""
