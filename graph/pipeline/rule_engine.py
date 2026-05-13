"""Rule engine: parse graph_rules.yaml and build RuleBasedExtractor instances.

Example graph_rules.yaml:

    extractors:
      - name: "frontend-api-calls"
        file_patterns: ["**/*.tsx", "**/*.ts"]
        rules:
          - match: 'axios\\.(get|post|put|delete)\\([''"]([^''"]+)[''"]'
            creates_node:
              label: "ApiCall"
              key_property: "path"
              properties:
                method: "$1"
                path: "$2"
            creates_edge:
              from_context: "enclosing_function"
              type: "CALLS_API"
              to: "{node}"
"""

from __future__ import annotations

import logging
import re
from fnmatch import fnmatch
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import (
    ExtractionContext,
    ExtractionResult,
    GraphEdge,
    GraphNode,
)

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class RuleBasedExtractor(BaseExtractor):
    """A dynamically configured extractor driven by regex rules from YAML."""

    def __init__(self, name: str, file_patterns: list[str], rules: list[dict]) -> None:
        self.name = name
        self._file_patterns = file_patterns
        self._rules = rules

    def can_handle(self, file_path: str, text: str) -> bool:
        fp = Path(file_path)
        return any(
            fp.match(pat) or fnmatch(str(fp), pat) for pat in self._file_patterns
        )

    def extract(
        self, file_path: str, text: str, context: ExtractionContext
    ) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name=self.name)
        lines = text.splitlines()

        for rule in self._rules:
            pattern = re.compile(rule["match"], re.IGNORECASE | re.MULTILINE)
            exclude = (
                re.compile(rule["exclude_match"]) if rule.get("exclude_match") else None
            )
            node_cfg = rule.get("creates_node", {})
            edge_cfg = rule.get("creates_edge", {})

            for m in pattern.finditer(text):
                raw_match = m.group(0)
                if exclude and exclude.search(raw_match):
                    continue

                def _sub(tmpl: str) -> str:
                    out = tmpl
                    for i, g in enumerate(m.groups(), start=1):
                        out = out.replace(f"${i}", g or "")
                    out = out.replace("{file_path}", file_path)
                    out = out.replace("{repository}", context.repository)
                    return out

                if node_cfg:
                    label = node_cfg.get("label", "CustomNode")
                    key_prop = node_cfg.get("key_property", "name")
                    raw_props: dict = node_cfg.get("properties", {})
                    props = {k: _sub(v) for k, v in raw_props.items()}
                    key_value = props.get(key_prop, raw_match[:80])
                    qualified = f"{label}:{context.repository}:{key_value}"
                    props.update(
                        {
                            "repository": context.repository,
                            "source_file": file_path,
                            "qualified_name": qualified,
                        }
                    )
                    node = GraphNode(
                        label=label,
                        key=key_prop,
                        key_value=qualified,
                        properties=props,
                        source="rule",
                    )
                    result.nodes.append(node)

                    # Enclosing function heuristic: find last 'function/method' above match
                    if edge_cfg.get("from_context") == "enclosing_function":
                        line_no = text[: m.start()].count("\n")
                        func_name = _find_enclosing_function(lines, line_no)
                        if func_name:
                            func_qname = f"Function:{context.repository}:{func_name}"
                            result.edges.append(
                                GraphEdge(
                                    from_label="Function",
                                    from_key="qualified_name",
                                    from_key_value=func_qname,
                                    to_label=label,
                                    to_key="qualified_name",
                                    to_key_value=qualified,
                                    rel_type=edge_cfg.get("type", "USES"),
                                    properties={"source_file": file_path},
                                )
                            )

        return result


def _find_enclosing_function(lines: list[str], line_no: int) -> str | None:
    """Scan backwards from line_no to find the nearest function/method declaration."""
    fn_pattern = re.compile(
        r"(?:function\s+(\w+)|(\w+)\s*\(|async\s+(\w+)\s*\(|def\s+(\w+)\s*\()",
        re.IGNORECASE,
    )
    for i in range(min(line_no, len(lines) - 1), max(0, line_no - 30), -1):
        m = fn_pattern.search(lines[i])
        if m:
            return next(g for g in m.groups() if g)
    return None


class RuleEngine:
    def __init__(self, rules_path: str) -> None:
        self._path = rules_path
        self._config: dict = {}
        self._load()

    def _load(self) -> None:
        if _yaml is None:
            logger.warning(
                "PyYAML not installed — rule engine disabled. Run: pip install pyyaml"
            )
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                self._config = _yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning("Failed to load %s: %s", self._path, exc)

    def build_extractors(self) -> list[RuleBasedExtractor]:
        extractors = []
        for entry in self._config.get("extractors", []):
            try:
                ext = RuleBasedExtractor(
                    name=entry.get("name", "unnamed"),
                    file_patterns=entry.get("file_patterns", []),
                    rules=entry.get("rules", []),
                )
                extractors.append(ext)
            except Exception as exc:
                logger.warning("Rule extractor parse error: %s", exc)
        return extractors
