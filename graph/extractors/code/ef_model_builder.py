"""EfModelBuilderExtractor — parses EF Core ModelBuilder / IEntityTypeConfiguration files.

Extracts actual database table names from:
  - b.ToTable("AbpPermissionGrants")
  - builder.Entity<X>().ToTable("name")
  - modelBuilder.Entity<X>(b => { b.ToTable("name"); })

These files are typically named *ModelBuilderExtensions.cs or
*EntityTypeConfiguration.cs and contain the fluent API table mapping.

Node types: Table (with the real DB table name)
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphNode
from graph.db import schema as S

logger = logging.getLogger(__name__)

_CS_EXT = ".cs"

# b.ToTable("AbpPermissionGrants")  or  .ToTable("name", schema)
_TO_TABLE_LITERAL = re.compile(
    r'\.ToTable\s*\(\s*"([^"]+)"',
    re.IGNORECASE,
)

# b.ToTable(SomePrefix + "Suffix")  — capture prefix expression AND string suffix
# e.g. ToTable(AbpXxxDbProperties.DbTablePrefix + "PermissionGrants")
_TO_TABLE_CONCAT = re.compile(
    r'\.ToTable\s*\(\s*(\w[\w.]*)\s*\+\s*"([^"]+)"',
    re.IGNORECASE,
)

# Quick check: must have any ToTable call
_HAS_TO_TABLE = re.compile(r'\.ToTable\s*\(', re.IGNORECASE)

# Known ABP table prefixes — used to reconstruct real table names from concat patterns
# Maps constant name fragment → prefix string
_KNOWN_PREFIXES: dict[str, str] = {
    "AbpCommonDbProperties": "Abp",
    "DbTablePrefix": "Abp",   # fallback when prefix var matches this name
}


def _resolve_prefix(prefix_expr: str) -> str:
    """Try to infer the string prefix from the constant expression in a concat pattern."""
    for key, val in _KNOWN_PREFIXES.items():
        if key in prefix_expr:
            return val
    return ""   # unknown prefix → skip (don't create wrong node)


class EfModelBuilderExtractor(BaseExtractor):
    """Extracts actual DB table names from EF Core ModelBuilder extension files."""

    def can_handle(self, file_path: str, text: str) -> bool:
        return (
            Path(file_path).suffix.lower() == _CS_EXT
            and bool(_HAS_TO_TABLE.search(text))
        )

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="EfModelBuilderExtractor")
        repository = context.repository
        seen: set[str] = set()

        def add_table(name: str) -> None:
            name = name.strip()
            if not name or len(name) < 2 or name in seen:
                return
            seen.add(name)
            qname = context.table_qname(name)
            result.nodes.append(GraphNode(
                label=S.LABEL_TABLE,
                key="qualified_name",
                key_value=qname,
                properties={
                    "qualified_name": qname,
                    "name": name,
                    "repository": repository,
                    "db_name": context.db_name,
                    "source_file": file_path,
                    "source": "ef_model_builder",
                },
            ))

        # 1. Hardcoded literal: .ToTable("AbpPermissionGrants")
        for m in _TO_TABLE_LITERAL.finditer(text):
            add_table(m.group(1))

        # 2. Concatenation: .ToTable(SomePrefix + "Suffix")
        #    Only add if we can resolve the prefix to avoid wrong names.
        for m in _TO_TABLE_CONCAT.finditer(text):
            prefix_expr = m.group(1)   # e.g. "AbpPermissionManagementDbProperties.DbTablePrefix"
            suffix = m.group(2)
            prefix = _resolve_prefix(prefix_expr)
            if prefix:
                add_table(prefix + suffix)

        return result
