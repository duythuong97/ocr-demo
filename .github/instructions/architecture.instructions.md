---
applyTo: "**/*.py"
---

# App Architecture

## Overview

This is a Flask app with 4 domain features: **search**, **ingest**, **retrieval**, **knowledge**.
External systems: Solr (fulltext), Qdrant (vectors), PostgreSQL (state), Neo4j (graph, optional).

---

## Module Map

```
ocr-demo/
├── app.py                  Entry point — calls web.create_app()
├── config.py               All env/config constants (SOLR_URL, QDRANT_URL, etc.)
├── services.py             Singleton registry (set by web.create_app, never import-time)
│
├── infra/                  ← Infrastructure adapters (reusable across features)
│   ├── __init__.py
│   ├── embedding.py        EmbedderProtocol (interface) + Embedder (OpenAI-compat HTTP impl)
│   ├── solr_store.py       Solr read/write wrapper
│   └── qdrant_store.py     Qdrant read/write adapter (chunk → embed → upsert + search)
│
├── ingest/                 ← Ingest feature — pipeline design
│   ├── __init__.py
│   ├── models.py           IndexJobConfig dataclass
│   ├── store.py            IndexingStateStore — PostgreSQL repo (jobs, files, state)
│   ├── worker.py           IndexingWorker — background thread, drives the pipeline
│   ├── source_manager.py   Load/save index source configs from JSON
│   ├── run_all.py          Run-all: VCS update + queue all sources
│   ├── chunkers/           File chunkers (text, code, excel, word, pptx…)
│   ├── readers/            File readers (default, pdf, html, image, excel…)
│   ├── pipeline/           Graph extraction pipeline (optional, Neo4j-only)
│   │   ├── registry.py     ExtractorRegistry
│   │   ├── rule_engine.py  YAML rule-based extractors
│   │   └── runner.py       PipelineRunner
│   ├── graph/              Neo4j graph client, writer, schema, entities
│   └── extractors/         Domain extractors (code, config, data, spec)
│
├── knowledge/              ← Knowledge feature (manual nodes/edges/texts)
│   ├── __init__.py
│   └── service.py          KnowledgeService — CRUD, apply_graph, apply_texts, CSV import
│
├── search/                 ← Search feature (fulltext + semantic)
│   ├── __init__.py
│   ├── fulltext.py         Solr search logic
│   ├── params.py           Query param parsing
│   └── result.py           Result normalisation
│
├── retrieval/              ← Retrieval / RAG feature
│   ├── __init__.py
│   ├── rag.py              RAG pipeline (retrieve → rerank → stream LLM)
│   ├── reranker.py         Reranker wrapper
│   ├── retrieval_service.py Qdrant semantic search (read-only)
│   ├── graph_service.py    Neo4j graph query service
│   ├── proxy_chat.py       LLM proxy
│   └── tools.py            Tool definitions for agent mode
│
└── web/                    ← Web layer (Flask blueprints, no business logic)
    ├── __init__.py         create_app() — wires services, registers blueprints
    ├── filters.py          Jinja2 filters
    ├── ingest_routes.py    /api/indexing/* endpoints
    ├── knowledge_routes.py /api/knowledge/* endpoints
    ├── retrieval_routes.py /api/retrieval/* + /api/chat/* endpoints
    ├── search_routes.py    /api/search endpoint
    ├── agent_routes.py     /api/agent/* endpoints
    └── chat_store.py       PostgreSQL chat history store
```

---

## Dependency Injection Pattern

This app follows a **manual DI pattern** — no DI container, `web/create_app()` is the composition root.

### Rule: ABC for every swappable service

Any service that could have multiple implementations MUST define an ABC (or `typing.Protocol`)
as its interface. The concrete class implements it. `web/create_app()` decides which impl to wire.

```
embedding.py
  EmbedderProtocol  (Protocol)        ← interface
  Embedder          (implementation)  ← OpenAI-compatible HTTP

retrieval/reranker.py
  BaseReranker      (ABC)             ← interface          [already done]
  LlmReranker       (implementation)  ← listwise LLM

infra/qdrant_store.py
  VectorStoreProtocol  (Protocol)     ← interface (to be defined when a second impl is added)
  QdrantStore          (implementation)

infra/solr_store.py
  FulltextStoreProtocol (Protocol)    ← interface (to be defined when a second impl is added)
  SolrStore             (implementation)
```

### ABC/Protocol placement rules

| Interface | Where it lives |
|---|---|
| `EmbedderProtocol` | `infra/embedding.py` (same file as `Embedder`) |
| `BaseReranker` | `retrieval/reranker.py` (same file as impls) |
| `VectorStoreProtocol` | `infra/qdrant_store.py` — define when a second vector backend is needed |
| `FulltextStoreProtocol` | `infra/solr_store.py` — define when a second fulltext backend is needed |

**Do NOT** define interfaces in a separate `interfaces/` or `abstractions/` folder. Keep the ABC/Protocol
co-located with its primary implementation.

### Writing a new swappable service

1. Define the ABC/Protocol first (in the same file as the concrete class).
2. Concrete class inherits from it (`class Embedder(EmbedderProtocol)` or just satisfies the Protocol structurally).
3. Type-hint constructor params with the ABC/Protocol, not the concrete class.
4. Wire the concrete instance in `web/__init__.py` — that is the **only** place that imports concrete classes.

```python
# infra/embedding.py — protocol + impl co-located
from typing import Protocol, runtime_checkable

@runtime_checkable
class EmbedderProtocol(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def get_config(self) -> dict: ...

class Embedder:          # satisfies EmbedderProtocol structurally
    ...

# infra/qdrant_store.py — type-hint with protocol
from infra.embedding import EmbedderProtocol

class QdrantStore:
    def __init__(self, qdrant_url: str, collection_name: str, embedder: EmbedderProtocol) -> None:
        ...

# web/__init__.py — composition root picks the concrete impl
from infra.embedding import Embedder          # concrete import ONLY here
_embedder = Embedder(cfg.SEMANTIC_MODEL, cfg.EMBEDDER_URL)
_qdrant_store = QdrantStore(cfg.QDRANT_URL, cfg.QDRANT_COLLECTION, _embedder)
```

Swapping the embedder in future = change **one line** in `web/__init__.py`, nothing else.

---

## Layer Rules

### `infra/` — Infrastructure adapters
- Pure I/O wrappers, no business logic.
- `qdrant_store.py` owns all QdrantClient usage (both write from ingest and read from retrieval).
- `solr_store.py` owns all pysolr usage for indexing writes; search reads may use `services.solr` directly.
- `embedding.py` (root level) has been removed — import from `infra.embedding` directly.

### `ingest/` — Ingest pipeline
- **Pipeline design**: file processing is a sequence of steps.
  Worker calls: `reader → chunker → solr_step → qdrant_step`.
- `store.py` (`IndexingStateStore`) is the PostgreSQL repository. It handles:
  `index_jobs`, `job_files`, `indexed_files` tables only.
  Knowledge tables (`knowledge_nodes`, `knowledge_edges`, `knowledge_texts`) are NOT here.
- `worker.py` (`IndexingWorker`) drives the loop; it must NOT contain parsing or embedding logic.
- Never create new files directly in `ingest/` root for a new file format — use `readers/` and `chunkers/`.

### `knowledge/` — Knowledge feature
- `knowledge/service.py` (`KnowledgeService`) is the only class allowed to read/write knowledge tables.
- Knowledge PostgreSQL tables are managed by `KnowledgeService`, NOT by `IndexingStateStore`.
- Knowledge feature depends on `ingest/store.py` for the PostgreSQL connection but nothing else from `ingest/`.

### `search/` — Search feature
- Fulltext search logic stays in `search/fulltext.py`.
- Semantic/vector search stays in `retrieval/retrieval_service.py` (reused in hybrid mode).
- Do NOT add search logic to routes.

### `retrieval/` — Retrieval / RAG
- `retrieval_service.py` is for semantic similarity search (Qdrant reads).
- RAG orchestration (prompt building, streaming) stays in `rag.py`.
- Do NOT import from `ingest/` in this package.

### `web/` — Web layer
- Routes only: parse request, call service, return response.
- No SQL, no Qdrant calls, no business logic in routes.
- All service instances come from `services.*` singletons.

---

## Module Locations (Completed Refactor)

| Symbol | Location |
|---|---|
| `IndexJobConfig` | `ingest/models.py` |
| `IndexingStateStore` | `ingest/store.py` |
| `IndexingWorker` | `ingest/worker.py` |
| `QdrantStore` (Qdrant write + read) | `infra/qdrant_store.py` |
| `SolrStore` (Solr write adapter) | `infra/solr_store.py` |
| `IngestService` (alias) | `ingest/ingest_service.py` — re-exports `QdrantStore` |
| `RetrievalService` (alias) | `retrieval/retrieval_service.py` — re-exports `QdrantStore` |
| `KnowledgeService` | `knowledge/service.py` |
| `KNOWLEDGE_TEMPLATES`, `_CSV_HEADERS` | `knowledge/service.py` |
| `embedding.py` | root `embedding.py` (acceptable as-is) |

---

## Adding New Code — Quick Guide

| What you're adding | Where it goes |
|---|---|
| New file format reader | `ingest/readers/your_reader.py` |
| New chunker | `ingest/chunkers/your_chunker.py` |
| New search param | `search/params.py` |
| New config value | `config.py` |
| New Flask route | `web/your_routes.py` + register in `web/__init__.py` |
| New Solr operation | `infra/solr_store.py` (target) |
| New Qdrant operation | `infra/qdrant_store.py` (target) |
| New knowledge type | `knowledge/service.py` |
