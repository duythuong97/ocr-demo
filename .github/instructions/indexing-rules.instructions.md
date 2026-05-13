---
applyTo: "ingest/**/*.py"
---

# Ingest Module Rules

> For overall app architecture see `.github/instructions/architecture.instructions.md`.

## 1. No Neo4j / Graph Extraction in Indexing Pipeline

**Never** insert or write data to Neo4j in the indexing pipeline.

- Do **not** call `self._pipeline_runner.run(...)` or any graph extraction method.
- Do **not** check `cfg.GRAPH_ENABLED` or `_GRAPH_AVAILABLE` to conditionally run graph code.
- Remove or leave commented any block guarded by `if _GRAPH_AVAILABLE and self._pipeline_runner and cfg.GRAPH_ENABLED`.
- `ExtractionContext` must not be instantiated in indexing paths.

## 2. No Fallback in Chunkers

**Never** silently fall back to another chunker when the primary chunker fails.

- `TreeSitterCodeChunker` must **not** fall back to `TextChunker` for unsupported extensions, failed parses, or empty AST results — raise `ValueError` instead.
- No chunker may catch its own errors and return results from a different chunker.
- If a file cannot be chunked by the assigned chunker, let the error propagate so the file is marked `failed` in the job and the root cause is visible in logs.

### Correct pattern

```python
ext = Path(file_path).suffix.lower()
if ext not in _LANGUAGE_BY_EXT:
    raise ValueError(f"Unsupported extension for TreeSitterCodeChunker: {ext!r}")

tree = parser.parse(source_bytes)
if tree is None:
    raise ValueError(f"tree-sitter parse returned None for {file_path!r}")

walk(tree.root_node)
if not chunks:
    raise ValueError(f"No useful AST nodes found in {file_path!r}")
```

### Anti-patterns (forbidden)

```python
# ❌ DO NOT do this
if ext not in _LANGUAGE_BY_EXT:
    return self._fallback.chunk(file_path, text)

# ❌ DO NOT do this
if not chunks:
    return self._fallback.chunk(file_path, text)
```

## 3. Imports at Top of File

All `import` statements must appear at the **top of the file**, grouped after the module docstring and `from __future__ import annotations`.

- **Never** place `import` inside a function, method, or conditional block.
- This applies to all imports including lazy ones (e.g. `import logging`, `import warnings`, `import chardet`).

### Correct pattern

```python
from __future__ import annotations

import logging
import warnings
from pathlib import Path

from ingest.chunkers.base import BaseChunker, SemanticChunk
```

### Anti-patterns (forbidden)

```python
# ❌ DO NOT do this — import inside a method
def chunk(self, file_path: str, text: str):
    import logging
    logging.getLogger(__name__).debug(...)

# ❌ DO NOT do this — inline import inside a conditional
if not chunks:
    import logging
    logging.getLogger(__name__).debug(...)

# ❌ DO NOT do this — lazy import inside a reader method
def _extract(self, path: Path) -> str:
    from ingest.readers.encoding_utils import read_text_auto
    return read_text_auto(path)
```

## 4. Ingest Module Placement Rules

**`ingest/` is a pipeline feature**, not a dumping ground. Follow these rules for new code:

### Where to put new code in `ingest/`

| What | Where |
|---|---|
| New file format support | `ingest/readers/` (reader) + `ingest/chunkers/` (chunker) |
| Job/file state data model | `ingest/models.py` — `IndexJobConfig` and similar dataclasses |
| SQLite state operations (jobs, files) | `ingest/store.py` — `IndexingStateStore` only |
| Background indexing thread | `ingest/worker.py` — `IndexingWorker` only |
| Qdrant write operations (embed + upsert) | `ingest/ingest_service.py` until moved to `infra/qdrant_store.py` |
| Source config load/save | `ingest/source_manager.py` |
| Run-all orchestration | `ingest/run_all.py` |

### Do NOT put in `ingest/`
- Knowledge management logic → belongs in `knowledge/service.py`
- Search/query logic → belongs in `search/`
- LLM/RAG logic → belongs in `retrieval/`
- New top-level "service" files that mix DB + business logic → split them

### SQLite table ownership
- `IndexingStateStore` owns: `index_jobs`, `job_files`, `indexed_files`
- `KnowledgeService` owns: `knowledge_nodes`, `knowledge_edges`, `knowledge_texts`
- Do NOT add knowledge table operations to `IndexingStateStore`.
