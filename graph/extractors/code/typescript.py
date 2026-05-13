"""TypeScript/JavaScript extractor.

Extracts:
  - HTTP API calls (axios, fetch, ky, superagent) → ApiCall nodes
  - External service calls (non-local URLs) → ExternalService nodes
  - React/Next.js page components → FrontendPage nodes
  - Enclosing function/component context for edges
"""
from __future__ import annotations

import re
import logging
from pathlib import Path

from graph.extractors.base import BaseExtractor
from graph.db.entities import ExtractionContext, ExtractionResult, GraphEdge, GraphNode
from graph.db import schema as S

logger = logging.getLogger(__name__)

_TS_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}

# axios.get('/path'), fetch('/path'), ky.get('/path')
# Angular: this.http.get<T>('/path'), this.httpClient.post<T>('/path', body)
_HTTP_CALL = re.compile(
    r'(?:axios|fetch|ky|got|superagent|(?:this\.)?http(?:Client)?)\s*[\.(]\s*'
    r'(?:get|post|put|patch|delete|request)\s*(?:<[^>]{0,80}>)?\s*\(\s*[`\'"]((?:/|https?://)[^\'"` ]{1,200})[`\'"]',
    re.IGNORECASE,
)
# Infer HTTP method from call
_METHOD_FROM_CALL = re.compile(
    r'(?:axios|ky|got|(?:this\.)?http(?:Client)?)\s*[\.(]\s*(get|post|put|patch|delete)\s*[\(<]',
    re.IGNORECASE,
)

# External URL pattern
_EXTERNAL_URL = re.compile(r'https?://([a-zA-Z0-9\-\.]+\.[a-z]{2,})', re.IGNORECASE)
_INTERNAL_HOST = re.compile(r'localhost|127\.0\.0|192\.168|10\.\d+\.\d+|\.local\b|\.internal\b', re.IGNORECASE)

# Next.js / React pages & components
_EXPORT_DEFAULT = re.compile(r'export\s+default\s+(?:function\s+)?(\w+)', re.MULTILINE)
_FUNCTION_DEF = re.compile(
    r'(?:export\s+)?(?:async\s+)?(?:function\s+(\w+)|const\s+(\w+)\s*=\s*(?:async\s+)?\()',
    re.MULTILINE,
)

# Angular @Injectable service class
_ANGULAR_INJECTABLE_RE = re.compile(
    r'@Injectable\s*\([^)]{0,200}\)\s*(?:export\s+)?class\s+(\w+)',
    re.DOTALL,
)

# Angular @Component({ selector: '...', ... }) class ComponentName
_ANGULAR_COMPONENT_RE = re.compile(
    r'@Component\s*\(\s*\{([^}]{0,800})\}\s*\)\s*(?:export\s+)?class\s+(\w+)',
    re.DOTALL,
)
_ANGULAR_SELECTOR_RE   = re.compile(r"selector\s*:\s*['\"`]([^'\"`]+)['\"`]")
_ANGULAR_TEMPLATE_RE   = re.compile(r"templateUrl\s*:\s*['\"`]([^'\"`]+)['\"`]")

# Angular router: { path: 'route', component: ComponentClass }
_ANGULAR_ROUTE_RE = re.compile(
    r"\{\s*path\s*:\s*['\"`]([^'\"`]*)['\"`]\s*,\s*component\s*:\s*(\w+)",
    re.IGNORECASE,
)

# Class definition — used to build class body spans
_CLASS_DEF_RE = re.compile(r'(?:export\s+)?(?:abstract\s+)?class\s+(\w+)', re.MULTILINE)

# Constructor parameter list — captures the full argument string
_CONSTRUCTOR_RE = re.compile(r'\bconstructor\s*\(([^)]{0,2000})\)', re.DOTALL)

# Single injected parameter: (private|public|protected|readonly) paramName: TypeName
_INJECT_PARAM_RE = re.compile(
    r'(?:private|public|protected|readonly)\s+\w+\s*:\s*([A-Z]\w+)'
)

# Angular HttpClient with a template-literal base variable:
#   this.http.get<T>(`${this.base}/employees/${empId}`)
# Captures the static path segment that follows the first ${...} expression.
_ANGULAR_HTTP_TPL = re.compile(
    r'(?:this\.)?http(?:Client)?\s*[\.(]\s*'
    r'(get|post|put|patch|delete|request)\s*(?:<[^>]{0,80}>)?\s*'
    r'\(`\$\{[^}]+\}((?:/[^`]{1,200})?)`',
    re.IGNORECASE,
)

# Angular / common framework types to skip from DI (they are not domain services)
_ANGULAR_DI_BUILTINS = re.compile(
    r'^(?:HttpClient|HttpBackend|HttpHandler|Router|ActivatedRoute|'
    r'ActivatedRouteSnapshot|RouterStateSnapshot|ChangeDetectorRef|'
    r'ElementRef|Renderer2|NgZone|DomSanitizer|Title|Meta|Location|'
    r'PlatformLocation|Injector|ComponentFactoryResolver|ApplicationRef|'
    r'Store|Actions|FormBuilder|MatDialog|MatSnackBar|MatDialogRef|'
    r'TranslateService|DatePipe|DecimalPipe|CurrencyPipe|'
    r'String|Number|Boolean|Object|Array)$'
)

_NEXTJS_PAGE_PATHS = {"pages/", "app/", "src/pages/", "src/app/"}


def _is_page_file(file_path: str) -> bool:
    fp = file_path.replace("\\", "/")
    return any(seg in fp for seg in _NEXTJS_PAGE_PATHS)


def _infer_method(text: str, pos: int) -> str:
    snippet = text[max(0, pos - 60): pos + 10]
    m = _METHOD_FROM_CALL.search(snippet)
    return m.group(1).upper() if m else "GET"


class TypeScriptApiCallExtractor(BaseExtractor):
    def can_handle(self, file_path: str, text: str) -> bool:
        return Path(file_path).suffix.lower() in _TS_EXTENSIONS

    def extract(self, file_path: str, text: str, context: ExtractionContext) -> ExtractionResult:
        result = ExtractionResult(source_file=file_path, extractor_name="TypeScriptApiCallExtractor")
        repository = context.repository
        lines = text.splitlines()
        func_spans = _build_function_spans(text)
        class_spans = _build_class_spans(text)
        # Maps class_name → Neo4j label for classes found in this file.
        # Populated early so HTTP-call and DI loops can reference it.
        class_label_map: dict[str, str] = {}

        # ── Frontend pages ────────────────────────────────────────────────────
        if _is_page_file(file_path):
            for m in _EXPORT_DEFAULT.finditer(text):
                comp_name = m.group(1)
                page_path = _derive_route_from_path(file_path)
                qname = f"{S.LABEL_FRONTEND_PAGE}:{repository}:{comp_name}"
                result.nodes.append(GraphNode(
                    label=S.LABEL_FRONTEND_PAGE,
                    key="qualified_name",
                    key_value=qname,
                    properties={
                        "qualified_name": qname,
                        "name": comp_name,
                        "route": page_path,
                        "repository": repository,
                        "source_file": file_path,
                    },
                ))

        # ── Angular @Component → FrontendComponent ────────────────────────────
        # Done before HTTP-call loop so class_label_map is populated first.
        for m in _ANGULAR_COMPONENT_RE.finditer(text):
            decorator_body = m.group(1)
            class_name = m.group(2)
            sel_m = _ANGULAR_SELECTOR_RE.search(decorator_body)
            tpl_m = _ANGULAR_TEMPLATE_RE.search(decorator_body)
            selector = sel_m.group(1) if sel_m else class_name
            template = tpl_m.group(1) if tpl_m else ""
            comp_qname = f"{S.LABEL_FRONTEND_COMPONENT}:{repository}:{class_name}"
            result.nodes.append(GraphNode(
                label=S.LABEL_FRONTEND_COMPONENT,
                key="qualified_name",
                key_value=comp_qname,
                properties={
                    "qualified_name": comp_qname,
                    "name": class_name,
                    "selector": selector,
                    "template_url": template,
                    "repository": repository,
                    "source_file": file_path,
                },
            ))
            class_label_map[class_name] = S.LABEL_FRONTEND_COMPONENT

        # ── Angular @Injectable service ────────────────────────────────────────
        # Done before HTTP-call loop so class_label_map is populated first.
        for m in _ANGULAR_INJECTABLE_RE.finditer(text):
            svc_class = m.group(1)
            svc_qname = f"{S.LABEL_SERVICE}:{repository}:{svc_class}"
            result.nodes.append(GraphNode(
                label=S.LABEL_SERVICE,
                key="qualified_name",
                key_value=svc_qname,
                properties={
                    "qualified_name": svc_qname,
                    "name": svc_class,
                    "repository": repository,
                    "source_file": file_path,
                    "framework": "Angular",
                },
            ))
            class_label_map[svc_class] = S.LABEL_SERVICE

        # ── Constructor DI → FrontendComponent/Service -[USES_SERVICE]→ Service ─
        # For each known class, parse its constructor and build dependency edges.
        for start_line, end_line, class_name in class_spans:
            if class_name not in class_label_map:
                continue
            cls_label = class_label_map[class_name]
            cls_qname = f"{cls_label}:{repository}:{class_name}"
            # Slice the class body text using character offsets
            lines_list = text.splitlines(keepends=True)
            class_body = "".join(lines_list[start_line: end_line + 1])
            m_ctor = _CONSTRUCTOR_RE.search(class_body)
            if not m_ctor:
                continue
            for param_m in _INJECT_PARAM_RE.finditer(m_ctor.group(1)):
                injected_type = param_m.group(1)
                if _ANGULAR_DI_BUILTINS.match(injected_type):
                    continue
                dep_qname = f"{S.LABEL_SERVICE}:{repository}:{injected_type}"
                result.nodes.append(GraphNode(
                    label=S.LABEL_SERVICE,
                    key="qualified_name",
                    key_value=dep_qname,
                    properties={
                        "name": injected_type,
                        "repository": repository,
                        "kind": "angular_service",
                    },
                ))
                result.edges.append(GraphEdge(
                    from_label=cls_label,
                    from_key="qualified_name",
                    from_key_value=cls_qname,
                    to_label=S.LABEL_SERVICE,
                    to_key="qualified_name",
                    to_key_value=dep_qname,
                    rel_type=S.REL_USES_SERVICE,
                ))

        # ── HTTP API calls ─────────────────────────────────────────────────────
        seen_paths: set[str] = set()
        for m in _HTTP_CALL.finditer(text):
            path = m.group(1).strip()
            if not path.startswith("/") and not path.startswith("http"):
                continue  # skip variable paths we can't resolve statically
            if path in seen_paths:
                continue
            seen_paths.add(path)

            method = _infer_method(text, m.start())
            line_no = text[:m.start()].count("\n")
            enclosing = _resolve_span(func_spans, line_no)

            api_qname = f"ApiCall:{repository}:{method}:{path}"
            result.nodes.append(GraphNode(
                label="ApiCall",
                key="qualified_name",
                key_value=api_qname,
                properties={
                    "qualified_name": api_qname,
                    "method": method,
                    "path": path,
                    "repository": repository,
                    "source_file": file_path,
                    "line": line_no + 1,
                },
                source="extracted",
            ))

            if enclosing:
                func_qname = f"{S.LABEL_FUNCTION}:{repository}:{enclosing}"
                result.nodes.append(GraphNode(
                    label=S.LABEL_FUNCTION,
                    key="qualified_name",
                    key_value=func_qname,
                    properties={
                        "qualified_name": func_qname,
                        "name": enclosing,
                        "repository": repository,
                        "source_file": file_path,
                    },
                ))
                result.edges.append(GraphEdge(
                    from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=func_qname,
                    to_label="ApiCall", to_key="qualified_name", to_key_value=api_qname,
                    rel_type=S.REL_CALLS_API,
                    properties={"method": method, "path": path, "line": line_no + 1},
                ))

            # Also link the enclosing class (Service/Component) → ApiCall
            enclosing_class = _resolve_class(class_spans, line_no)
            if enclosing_class and enclosing_class in class_label_map:
                cls_label = class_label_map[enclosing_class]
                cls_qname = f"{cls_label}:{repository}:{enclosing_class}"
                result.edges.append(GraphEdge(
                    from_label=cls_label,
                    from_key="qualified_name",
                    from_key_value=cls_qname,
                    to_label="ApiCall",
                    to_key="qualified_name",
                    to_key_value=api_qname,
                    rel_type=S.REL_CALLS_API,
                    properties={"method": method, "path": path, "line": line_no + 1},
                ))

        # ── Angular HttpClient template-literal calls ──────────────────────────
        # Handles: this.http.get<T>(`${this.base}/employees/${id}`)
        # Normalises ${expr} segments in the path to {param} placeholders.
        for m in _ANGULAR_HTTP_TPL.finditer(text):
            raw_path = (m.group(2) or "").strip()
            if not raw_path:
                continue
            path = re.sub(r'\$\{[^}]+\}', '{param}', raw_path)
            if path in seen_paths:
                continue
            seen_paths.add(path)

            method = m.group(1).upper()
            line_no = text[: m.start()].count("\n")
            enclosing = _resolve_span(func_spans, line_no)

            api_qname = f"ApiCall:{repository}:{method}:{path}"
            result.nodes.append(GraphNode(
                label="ApiCall",
                key="qualified_name",
                key_value=api_qname,
                properties={
                    "qualified_name": api_qname,
                    "method": method,
                    "path": path,
                    "repository": repository,
                    "source_file": file_path,
                    "line": line_no + 1,
                },
                source="extracted",
            ))

            if enclosing:
                func_qname = f"{S.LABEL_FUNCTION}:{repository}:{enclosing}"
                result.nodes.append(GraphNode(
                    label=S.LABEL_FUNCTION,
                    key="qualified_name",
                    key_value=func_qname,
                    properties={
                        "qualified_name": func_qname,
                        "name": enclosing,
                        "repository": repository,
                        "source_file": file_path,
                    },
                ))
                result.edges.append(GraphEdge(
                    from_label=S.LABEL_FUNCTION, from_key="qualified_name", from_key_value=func_qname,
                    to_label="ApiCall", to_key="qualified_name", to_key_value=api_qname,
                    rel_type=S.REL_CALLS_API,
                    properties={"method": method, "path": path, "line": line_no + 1},
                ))

            enclosing_class = _resolve_class(class_spans, line_no)
            if enclosing_class and enclosing_class in class_label_map:
                cls_label = class_label_map[enclosing_class]
                cls_qname = f"{cls_label}:{repository}:{enclosing_class}"
                result.edges.append(GraphEdge(
                    from_label=cls_label,
                    from_key="qualified_name",
                    from_key_value=cls_qname,
                    to_label="ApiCall",
                    to_key="qualified_name",
                    to_key_value=api_qname,
                    rel_type=S.REL_CALLS_API,
                    properties={"method": method, "path": path, "line": line_no + 1},
                ))

        # ── External service calls ─────────────────────────────────────────────
        seen_hosts: set[str] = set()
        for m in _EXTERNAL_URL.finditer(text):
            host = m.group(1)
            if _INTERNAL_HOST.search(host) or host in seen_hosts:
                continue
            seen_hosts.add(host)
            ext_qname = f"{S.LABEL_EXTERNAL_SERVICE}:{host}"
            result.nodes.append(GraphNode(
                label=S.LABEL_EXTERNAL_SERVICE,
                key="qualified_name",
                key_value=ext_qname,
                properties={
                    "qualified_name": ext_qname,
                    "name": host,
                    "host": host,
                    "source_file": file_path,
                },
            ))

        # ── Angular router: { path: '...', component: Cls } ───────────────────
        # Creates FrontendPage + CONTAINS edge to FrontendComponent.
        for m in _ANGULAR_ROUTE_RE.finditer(text):
            route_path = m.group(1)
            comp_class = m.group(2)
            page_qname = f"{S.LABEL_FRONTEND_PAGE}:{repository}:{comp_class}"
            if not any(n.key_value == page_qname for n in result.nodes):
                result.nodes.append(GraphNode(
                    label=S.LABEL_FRONTEND_PAGE,
                    key="qualified_name",
                    key_value=page_qname,
                    properties={
                        "qualified_name": page_qname,
                        "name": comp_class,
                        "route": f"/{route_path.lstrip('/')}",
                        "repository": repository,
                        "source_file": file_path,
                    },
                ))
            # FrontendPage -[CONTAINS]→ FrontendComponent
            comp_qname = f"{S.LABEL_FRONTEND_COMPONENT}:{repository}:{comp_class}"
            result.edges.append(GraphEdge(
                from_label=S.LABEL_FRONTEND_PAGE,
                from_key="qualified_name",
                from_key_value=page_qname,
                to_label=S.LABEL_FRONTEND_COMPONENT,
                to_key="qualified_name",
                to_key_value=comp_qname,
                rel_type=S.REL_CONTAINS,
            ))

        return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_class_spans(text: str) -> list[tuple[int, int, str]]:
    """Return [(start_line, end_line, class_name), ...] for each class block.

    Uses brace-counting to find the matching closing brace of each class.
    """
    spans = []
    for m in _CLASS_DEF_RE.finditer(text):
        class_name = m.group(1)
        brace_start = text.find("{", m.end())
        if brace_start == -1:
            continue
        depth = 1
        pos = brace_start + 1
        while pos < len(text) and depth > 0:
            if text[pos] == "{":
                depth += 1
            elif text[pos] == "}":
                depth -= 1
            pos += 1
        start_line = text[: m.start()].count("\n")
        end_line = text[:pos].count("\n")
        spans.append((start_line, end_line, class_name))
    return spans


def _resolve_class(spans: list[tuple[int, int, str]], line_no: int) -> str | None:
    for start, end, name in spans:
        if start <= line_no <= end:
            return name
    return None


def _build_function_spans(text: str) -> list[tuple[int, str]]:
    spans = []
    for m in _FUNCTION_DEF.finditer(text):
        name = m.group(1) or m.group(2)
        if name:
            line_no = text[:m.start()].count("\n")
            spans.append((line_no, name))
    return sorted(spans, key=lambda x: x[0])


def _resolve_span(spans: list[tuple[int, str]], line_no: int) -> str | None:
    result = None
    for span_line, name in spans:
        if span_line <= line_no:
            result = name
        else:
            break
    return result


def _derive_route_from_path(file_path: str) -> str:
    fp = file_path.replace("\\", "/")
    for prefix in ("pages/", "src/pages/", "app/", "src/app/"):
        idx = fp.find(prefix)
        if idx != -1:
            route = fp[idx + len(prefix):]
            route = route.replace("index.tsx", "").replace("index.ts", "").replace("index.jsx", "").replace("index.js", "")
            route = route.replace(".tsx", "").replace(".ts", "").replace(".jsx", "").replace(".js", "")
            return "/" + route.strip("/")
    return file_path
