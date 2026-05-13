# OCR Demo Runbook

## Target Architecture

See `.github/instructions/architecture.instructions.md` for the full module map and placement rules.

**4 Domain Features:** search · ingest · retrieval · knowledge
**3 External Systems:** Solr (fulltext) · Qdrant (vectors) · Neo4j (graph, optional)
**State:** SQLite at `data/indexing.db`

## Core Paths

| Concern | Current file | Target (pending refactor) |
|---|---|---|
| Entry point | `app.py` | unchanged |
| Config | `config.py` | unchanged |
| Service singletons | `services.py` | unchanged |
| Embedder | `embedding.py` | `infra/embedder.py` |
| Ingest job config (dataclass) | `ingest/models.py` | ✅ |
| SQLite state store | `ingest/store.py` | ✅ |
| Indexing worker thread | `ingest/worker.py` | ✅ |
| Qdrant write adapter | `ingest/ingest_service.py` | `infra/qdrant_store.py` |
| Knowledge CRUD | `knowledge/service.py` | ✅ |
| Source config | `ingest/source_manager.py` | unchanged |
| Run-all | `ingest/run_all.py` | unchanged |
| Fulltext search | `search/fulltext.py` | unchanged |
| Qdrant read / RAG | `retrieval/retrieval_service.py`, `retrieval/rag.py` | unchanged |

## Start-Up Checklist
1. Activate environment and install dependencies.
2. Ensure Solr is running and healthy.
3. Start Flask app and confirm route prefix behavior.

Suggested local commands:
- pip install -r requirements.txt
- docker compose up -d solr solr-init
- flask --app app run --port 5100

## High-Value Endpoints
- GET /api/search
- POST /api/chat
- GET /api/chat/status
- GET /api/indexing/sources
- POST /api/indexing/sources
- POST /api/indexing/start
- POST /api/indexing/run-all
- POST /api/indexing/stop
- GET /api/indexing/status
- POST /api/indexing/clear/solr
- POST /api/indexing/clear/qdrant
- POST /api/indexing/clear/sqlite
- POST /api/indexing/clear/all

When APP_PREFIX is enabled (default ocr-search), call APIs under /ocr-search/... in browser/client contexts.

## Indexing Troubleshooting Flow
1. Check active job via /api/indexing/status.
2. Verify there is no existing queued/running job for the same source.
3. Confirm root_path exists and contains repository folder matching name.
4. Inspect logs/indexing.log for per-file failures.
5. If state is stale from interrupted runs, verify worker recovery behavior (processing to pending).
6. Retry with a single source before using run-all.

## Semantic Search Troubleshooting Flow
1. Confirm semantic service availability at startup logs.
2. Check query mode (semantic or hybrid).
3. Validate SEMANTIC_MIN_SCORE in config.py and .env.
4. For SQL-like queries, verify lexical post-filter and fallback behavior.
5. If needed, clear qdrant and reindex.

## Safe Reset Order
Use targeted reset first:
1. clear/solr when keyword results are stale.
2. clear/qdrant when semantic vectors are stale.
3. clear/sqlite when job history is inconsistent.
4. clear/all only when full rebuild is intended.

## Notes For Changes
- Keep API contracts stable for static/indexing.js and templates/indexing.html.
- Preserve source deduplication logic by root_path + repository_path.
- Preserve cancel_requested semantics in running jobs.
