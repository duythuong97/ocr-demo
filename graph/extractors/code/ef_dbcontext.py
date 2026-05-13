"""EfDbContextExtractor — Entity Framework DbContext parser for C# files.

Detects classes that inherit from DbContext / IdentityDbContext / etc. and
extracts their DbSet<TEntity> properties, which represent ORM table mappings.

Extracts:
  - DbContext class → Class node
  - DbSet<Order> Orders → Table node "Order" + Class READS_FROM/WRITES_TO Table
  - [Table("tbl_orders")] attribute on entity → canonical table name override

Node types: Class (DbContext subclass), Table (entity/DB table)
Relationships: READS_FROM, WRITES_TO (DbContext → Table)
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphEdge, GraphNode
from graph.db import schema as S

logger = logging.getLogger(__name__)

_CS_EXT = ".cs"

# class SomeContext : DbContext  /  : ApplicationDbContext  /  : IdentityDbContext<User>
# Also matches ABP-style: : AbpDbContext<T>  /  : IdentityDbContext<User, Role>
# The base class may contain "DbContext" as a suffix (e.g. AbpDbContext) or standalone word.
_DBCONTEXT_CLASS = re.compile(
    r'class\s+(\w+)\s*(?:<[^>]*>)?\s*:\s*[\w<>,\s]*DbContext\b',
    re.IGNORECASE,
)

# public DbSet<OrderEntity> Orders { get; set; }
_DBSET = re.compile(
    r'(?:public|protected|internal)\s+(?:virtual\s+)?DbSet\s*<\s*(\w+)\s*>\s+(\w+)\s*[{;]',
    re.IGNORECASE,
)

# [Table("tbl_name")] or [Table("tbl_name", Schema = "dbo")]
_TABLE_ATTR = re.compile(
    r'\[Table\s*\(\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# namespace Foo.Bar  (to build qualified name)
_NAMESPACE = re.compile(r'^\s*namespace\s+([\w.]+)', re.MULTILINE)


class EfDbContextExtractor(BaseExtractor):
    def can_handle(self, file_path: str, text: str) -> bool:
        return (
            Path(file_path).suffix.lower() == _CS_EXT
            and "DbContext" in text
            and "DbSet" in text
        )

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="EfDbContextExtractor")
        repository = context.repository

        m_ns = _NAMESPACE.search(text)
        namespace = m_ns.group(1) if m_ns else ""

        # Find all DbContext subclasses in this file
        for m_ctx in _DBCONTEXT_CLASS.finditer(text):
            ctx_name = m_ctx.group(1)
            ctx_qname = f"{namespace}.{ctx_name}" if namespace else ctx_name
            ctx_node_qname = f"Class:{ctx_qname}"

            result.nodes.append(GraphNode(
                label=S.LABEL_CLASS,
                key="qualified_name",
                key_value=ctx_node_qname,
                properties={
                    "name": ctx_name,
                    "qualified_name": ctx_qname,
                    "namespace": namespace,
                    "repository": repository,
                    "source_file": file_path,
                    "kind": "ef_dbcontext",
                },
            ))

            # Scan the whole file for DbSet<> properties
            # (EF usually has one DbContext per file, so scanning full text is fine)
            for m_ds in _DBSET.finditer(text):
                entity_name = m_ds.group(1)   # e.g. "OrderEntity"
                prop_name = m_ds.group(2)      # e.g. "Orders"

                # Use entity name as table name (snake_case or as-is)
                table_name = entity_name
                table_qname = context.table_qname(table_name)

                result.nodes.append(GraphNode(
                    label=S.LABEL_TABLE,
                    key="qualified_name",
                    key_value=table_qname,
                    properties={
                        "name": table_name,
                        "entity_class": entity_name,
                        "dbset_property": prop_name,
                        "repository": repository,
                        "db_name": context.db_name,
                        "source_file": file_path,
                        "kind": "ef_entity",
                    },
                ))

                # DbContext reads from AND writes to the table (EF is bi-directional)
                for rel in (S.REL_READS_FROM, S.REL_WRITES_TO):
                    result.edges.append(GraphEdge(
                        from_label=S.LABEL_CLASS,
                        from_key="qualified_name",
                        from_key_value=ctx_node_qname,
                        to_label=S.LABEL_TABLE,
                        to_key="qualified_name",
                        to_key_value=table_qname,
                        rel_type=rel,
                        properties={
                            "via": "entity_framework",
                            "dbset_property": prop_name,
                        },
                    ))

        return result
