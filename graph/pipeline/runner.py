"""PipelineRunner: orchestrates extraction and graph writing for one file."""
from __future__ import annotations

import logging

from graph.db.entities import ExtractionContext, ExtractionResult, GraphEdge, GraphNode
from graph.db.writer import GraphWriter
from graph.db import schema as S
from graph.pipeline.registry import ExtractorRegistry

logger = logging.getLogger(__name__)


class PipelineRunner:
    """Runs all applicable extractors on a file and writes to Neo4j.

    Usage::

        runner = PipelineRunner(registry=registry, writer=graph_writer)
        result = runner.run(file_path, text, context)
    """

    def __init__(self, registry: ExtractorRegistry, writer: GraphWriter) -> None:
        self._registry = registry
        self._writer = writer

    def run(
        self,
        file_path: str,
        text: str,
        context: ExtractionContext,
    ) -> ExtractionResult:
        extractors = self._registry.extractors_for(file_path, text)
        if not extractors:
            return ExtractionResult(source_file=file_path)

        # Clean up stale nodes from previous index of this file
        self._writer.delete_file_nodes(file_path)

        # Always ensure the Repository node exists in the graph
        combined = _make_repository_result(context)
        for extractor in extractors:
            try:
                result = extractor.extract(file_path, text, context)
                result.source_file = file_path
                combined = combined.merge(result)
            except Exception as exc:
                logger.warning(
                    "Extractor %s failed on %s: %s",
                    type(extractor).__name__, file_path, exc, exc_info=True,
                )

        if not combined.is_empty():
            self._writer.write(combined)
            logger.info(
                "Pipeline [%s]: %d nodes, %d edges → Neo4j",
                file_path, len(combined.nodes), len(combined.edges),
            )

        return combined


def _make_repository_result(context: ExtractionContext) -> ExtractionResult:
    """Return an ExtractionResult that unconditionally upserts the Repository node."""
    result = ExtractionResult(extractor_name="PipelineRunner:repo")
    if not context.repository:
        return result
    result.nodes.append(GraphNode(
        label=S.LABEL_REPOSITORY,
        key="qualified_name",
        key_value=context.repo_qname(),
        properties={
            "name": context.repository,
            "source": context.source or "git",
            "vcs_url": context.vcs_url,
            "repository_path": context.repository_path,
            "owner": context.repo_owner,
            "team": context.team_name,
        },
    ))
    return result
