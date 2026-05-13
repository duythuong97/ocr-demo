"""Module-level service singletons — initialised by web.create_app()."""
from __future__ import annotations

# All set to None on startup; web.create_app() replaces them before serving.
solr = None               # pysolr.Solr  (search reads only)
solr_store = None         # SolrStore    (infra/solr_store.py — write adapter)
ingest_service = None     # QdrantStore  (backward-compat alias; same instance as retrieval_service)
retrieval_service = None  # QdrantStore  (infra/qdrant_store.py — write + read)
indexing_store = None     # IndexingStateStore
indexing_worker = None    # IndexingWorker
socketio = None           # flask_socketio.SocketIO
knowledge_service = None  # KnowledgeService
chat_store = None         # ChatStore
graph_service = None      # GraphService
