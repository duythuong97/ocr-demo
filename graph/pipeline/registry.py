"""ExtractorRegistry: register and discover extractors.

Extractors are registered either:
  - programmatically via register()
  - automatically by load_defaults() which imports built-in extractors

Adding a new language/format:
  1. Implement BaseExtractor in ingest/extractors/code/your_lang.py
  2. Call registry.register(YourExtractor()) in your app startup
     OR place it in load_defaults() below.
"""
from __future__ import annotations

import logging
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.extractors.code.csharp import CSharpSqlExtractor
from graph.extractors.code.ef_dbcontext import EfDbContextExtractor
from graph.extractors.code.ef_model_builder import EfModelBuilderExtractor
from graph.extractors.code.angular_module import NgModuleExtractor
from graph.extractors.code.typescript import TypeScriptApiCallExtractor
from graph.extractors.config.appsettings import AppSettingsExtractor
from graph.extractors.config.cronjob import CronJobExtractor
from graph.extractors.config.csproj import CsprojExtractor
from graph.extractors.config.packages_config import PackagesConfigExtractor
from graph.extractors.config.webconfig import WebConfigExtractor
from graph.extractors.data.oracle_ddl import OracleDdlExtractor
from graph.extractors.data.oracle_plsql import OraclePlSqlExtractor
from graph.extractors.data.sqlplus import SqlPlusScriptExtractor
from graph.extractors.data.csv_table import CsvTableExtractor
from graph.extractors.data.csv_repository import CsvRepositoryExtractor
from graph.extractors.data.xml_sql import XmlSqlExtractor
from graph.extractors.data.yaml_jobs import YamlJobsExtractor
from graph.extractors.spec.openapi import OpenApiExtractor
from graph.extractors.spec.resx import ResxExtractor

logger = logging.getLogger(__name__)

# Optional AST extractors — require tree-sitter; degrade gracefully.
try:
    from graph.extractors.code.ts_ast_extractor import TypeScriptAstExtractor as _TsAst
except Exception as _exc:  # noqa: BLE001
    _TsAst = None  # type: ignore[assignment]
    logger.debug("TypeScriptAstExtractor not available: %s", _exc)

try:
    from graph.extractors.code.cs_ast_extractor import CSharpAstExtractor as _CsAst
except Exception as _exc:  # noqa: BLE001
    _CsAst = None  # type: ignore[assignment]
    logger.debug("CSharpAstExtractor not available: %s", _exc)

try:
    from graph.extractors.code.python_ast_extractor import PythonAstExtractor as _PyAst
except Exception as _exc:  # noqa: BLE001
    _PyAst = None  # type: ignore[assignment]
    logger.debug("PythonAstExtractor not available: %s", _exc)


class ExtractorRegistry:
    def __init__(self) -> None:
        self._extractors: list[BaseExtractor] = []

    def register(self, extractor: BaseExtractor) -> "ExtractorRegistry":
        self._extractors.append(extractor)
        logger.debug("Registered extractor: %s", type(extractor).__name__)
        return self

    def extractors_for(self, file_path: str, text: str) -> list[BaseExtractor]:
        """Return all extractors that claim to handle this file."""
        return [e for e in self._extractors if e.can_handle(file_path, text)]

    def load_defaults(self) -> "ExtractorRegistry":
        """Register all built-in extractors."""
        # Oracle DDL first — pre-seeds Table nodes before PL/SQL extractor runs
        self.register(OracleDdlExtractor())
        self.register(OraclePlSqlExtractor())
        # SQL*Plus runner scripts (.sql with EXEC/CALL but no CREATE blocks)
        self.register(SqlPlusScriptExtractor())
        # CSV table inventory (scheme,name,description) — alternative to DDL parsing
        self.register(CsvTableExtractor())
        # CSV repository mapping (class_name,schema,table,operation) — MyBatis / manual
        self.register(CsvRepositoryExtractor())
        # YAML job definitions (steps: plsql / sqlplus / csharp_exe / shell)
        self.register(YamlJobsExtractor())
        # .NET project structure + config
        self.register(CsprojExtractor())
        self.register(PackagesConfigExtractor())
        self.register(AppSettingsExtractor())
        self.register(WebConfigExtractor())
        self.register(EfDbContextExtractor())
        self.register(EfModelBuilderExtractor())
        self.register(CSharpSqlExtractor())
        # Angular / TypeScript
        self.register(NgModuleExtractor())
        self.register(TypeScriptApiCallExtractor())
        # Specs / contracts
        self.register(OpenApiExtractor())
        self.register(ResxExtractor())
        # Infra / config
        self.register(XmlSqlExtractor())
        self.register(CronJobExtractor())

        # AST call-graph extractors (optional; only registered when available)
        if _TsAst is not None:
            self.register(_TsAst())
        if _CsAst is not None:
            self.register(_CsAst())
        if _PyAst is not None:
            self.register(_PyAst())

        return self

    def load_rules(self, rules_path: str) -> "ExtractorRegistry":
        """Load YAML rule-based extractors from graph_rules.yaml."""
        p = Path(rules_path)
        if not p.exists():
            logger.debug("No graph_rules.yaml at %s — skipping rule-based extractors", rules_path)
            return self
        try:
            from graph.pipeline.rule_engine import RuleEngine
            engine = RuleEngine(rules_path)
            rule_extractors = engine.build_extractors()
            for extractor in rule_extractors:
                self.register(extractor)
            logger.info("Loaded %d rule-based extractors from %s", len(rule_extractors), rules_path)
        except Exception as exc:
            logger.warning("Failed to load graph_rules.yaml: %s", exc)
        return self
