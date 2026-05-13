"""Neo4j schema: constraints and indexes.

Call apply_schema() once at startup.  All node labels and relationship types
are defined as constants here so extractors import from one place.
"""
from __future__ import annotations

import logging

from graph.db.client import GraphClient

logger = logging.getLogger(__name__)

# ── Node label constants ──────────────────────────────────────────────────────

# ── Tầng Landscape ────────────────────────────────────────────────────────────
LABEL_API_SERVICE      = "ApiService"        # runtime service/deployment unit
LABEL_DATABASE         = "Database"          # DBMS instance/cluster
LABEL_FRONTEND_APP     = "FrontendApp"       # Angular/React/Vue app
LABEL_JOB_PLATFORM     = "JobPlatform"       # JP1, Airflow, cron
LABEL_STORAGE          = "Storage"           # S3, NAS, Blob
LABEL_EXTERNAL_SERVICE = "ExternalService"   # SSO, Payment, ...

# ── Tầng Project/Module (Layer 2) ─────────────────────────────────────────────
LABEL_PROJECT          = "Project"           # csproj, Angular app, PLSQL source set
LABEL_MODULE           = "Module"            # C# namespace, Angular module, PL/SQL package

# ── Tầng Logic (Layer 3) ─────────────────────────────────────────────────────
LABEL_API_CONTROLLER   = "ApiController"     # HTTP controller class
LABEL_SERVICE_CLASS    = "ServiceClass"      # business logic class / repository class
LABEL_REPOSITORY_CLASS = "RepositoryClass"   # data-access class
LABEL_PLSQL_PACKAGE    = "PLSQLPackage"      # PL/SQL package header + body
LABEL_PROCEDURE        = "Procedure"         # stored procedure
LABEL_SQL_FUNCTION     = "SQLFunction"       # PL/SQL function
LABEL_TRIGGER          = "Trigger"           # PL/SQL trigger
LABEL_JOB              = "Job"               # batch job, cron job
LABEL_FRONTEND_COMPONENT = "FrontendComponent"
LABEL_API_ENDPOINT     = "ApiEndpoint"
LABEL_EVENT_TOPIC      = "EventTopic"

# ── Tầng Data (Layer 4) ───────────────────────────────────────────────────────
LABEL_TABLE            = "Table"
LABEL_COLUMN           = "Column"
LABEL_DIRECTORY        = "Directory"
LABEL_FILE             = "File"
LABEL_DOCUMENT         = "Document"

# ── Legacy / generic labels (kept for backward compatibility) ─────────────────
# Extractors may still emit these; treat as aliases until fully migrated
LABEL_FUNCTION         = "Function"          # use LABEL_PROCEDURE / LABEL_SQL_FUNCTION instead
LABEL_CLASS            = "Class"             # use LABEL_PLSQL_PACKAGE / LABEL_API_CONTROLLER instead
LABEL_SERVICE          = "Service"           # use LABEL_PROJECT instead
LABEL_DOMAIN           = "Domain"
LABEL_REPOSITORY       = "Repository"        # VCS repo node
LABEL_API_GATEWAY      = "ApiGateway"
LABEL_FRONTEND_PAGE    = "FrontendPage"
LABEL_WORKFLOW         = "Workflow"
LABEL_TASK             = "Task"
LABEL_TASK_STEP        = "TaskStep"
# Backward-compat alias: code that imports LABEL_CRON_JOB still works unchanged
LABEL_CRON_JOB         = LABEL_WORKFLOW

# ── Relationship type constants ───────────────────────────────────────────────
# Data operation edges (DML)
REL_READS_FROM   = "READS_FROM"    # SELECT
REL_WRITES_TO    = "WRITES_TO"     # generic write (MERGE/UPSERT/CALL/unknown)
REL_INSERTS_INTO = "INSERTS_INTO"  # INSERT
REL_UPDATES      = "UPDATES"       # UPDATE
REL_DELETES_FROM = "DELETES_FROM"  # DELETE
REL_TRUNCATES    = "TRUNCATES"     # TRUNCATE
# Legacy aliases (kept so old code still imports without error)
REL_READS  = REL_READS_FROM
REL_WRITES = REL_WRITES_TO
REL_BELONGS_TO = "BELONGS_TO"
REL_HANDLED_BY = "HANDLED_BY"
REL_TRIGGERS = "TRIGGERS"
REL_CALLS_API = "CALLS_API"
REL_CALLS_EXTERNAL = "CALLS_EXTERNAL"
REL_DEFINED_IN = "DEFINED_IN"
REL_PUBLISHES = "PUBLISHES"
REL_SUBSCRIBES = "SUBSCRIBES"
REL_CALLS = "CALLS"
REL_IMPORTS = "IMPORTS"
REL_INSTANTIATES = "INSTANTIATES"
REL_USES_SERVICE = "USES_SERVICE"

# New — repository / ownership
REL_HOSTS = "HOSTS"
REL_CONTAINS = "CONTAINS"
REL_OWNS = "OWNS"
REL_PROVIDES_API = "PROVIDES_API"

# New — workflow
REL_DEFINES_WORKFLOW = "DEFINES_WORKFLOW"
REL_HAS_TASK = "HAS_TASK"
REL_HAS_STEP = "HAS_STEP"
REL_EXECUTES = "EXECUTES"
REL_DEPENDS_ON = "DEPENDS_ON"

# New — documentation / references
REL_REFERENCES = "REFERENCES"
REL_DOCUMENTED_BY = "DOCUMENTED_BY"
REL_IMPLEMENTS = "IMPLEMENTS"
REL_DEFINED_BY = "DEFINED_BY"
REL_MENTIONS = "MENTIONS"

# ── Schema DDL ────────────────────────────────────────────────────────────────
# (qualified_name is the universal unique key across all code-layer nodes)
_CONSTRAINTS = [
    ("constraint_table_name",    f"CREATE CONSTRAINT constraint_table_name    IF NOT EXISTS FOR (n:{LABEL_TABLE})    REQUIRE n.qualified_name IS UNIQUE"),
    ("constraint_function_qn",   f"CREATE CONSTRAINT constraint_function_qn   IF NOT EXISTS FOR (n:{LABEL_FUNCTION}) REQUIRE n.qualified_name IS UNIQUE"),
    ("constraint_class_qn",      f"CREATE CONSTRAINT constraint_class_qn      IF NOT EXISTS FOR (n:{LABEL_CLASS})    REQUIRE n.qualified_name IS UNIQUE"),
    ("constraint_service_class_qn", f"CREATE CONSTRAINT constraint_service_class_qn IF NOT EXISTS FOR (n:{LABEL_SERVICE_CLASS}) REQUIRE n.qualified_name IS UNIQUE"),
    ("constraint_service_name",  f"CREATE CONSTRAINT constraint_service_name  IF NOT EXISTS FOR (n:{LABEL_SERVICE})  REQUIRE n.qualified_name IS UNIQUE"),
    ("constraint_domain_name",   f"CREATE CONSTRAINT constraint_domain_name   IF NOT EXISTS FOR (n:{LABEL_DOMAIN})   REQUIRE n.qualified_name IS UNIQUE"),
    ("constraint_api_key",       f"CREATE CONSTRAINT constraint_api_key       IF NOT EXISTS FOR (n:{LABEL_API_ENDPOINT}) REQUIRE n.qualified_name IS UNIQUE"),
    ("constraint_workflow_key",  f"CREATE CONSTRAINT constraint_workflow_key  IF NOT EXISTS FOR (n:{LABEL_WORKFLOW})  REQUIRE n.qualified_name IS UNIQUE"),
    ("constraint_task_key",      f"CREATE CONSTRAINT constraint_task_key      IF NOT EXISTS FOR (n:{LABEL_TASK})      REQUIRE n.qualified_name IS UNIQUE"),
    ("constraint_document_key",  f"CREATE CONSTRAINT constraint_document_key  IF NOT EXISTS FOR (n:{LABEL_DOCUMENT})  REQUIRE n.qualified_name IS UNIQUE"),
    ("constraint_file_path",     f"CREATE CONSTRAINT constraint_file_path     IF NOT EXISTS FOR (n:{LABEL_FILE})     REQUIRE n.qualified_name IS UNIQUE"),
    ("constraint_repo_name",     f"CREATE CONSTRAINT constraint_repo_name     IF NOT EXISTS FOR (n:{LABEL_REPOSITORY}) REQUIRE n.qualified_name IS UNIQUE"),
]

_INDEXES = [
    ("index_table_name",         f"CREATE INDEX index_table_name         IF NOT EXISTS FOR (n:{LABEL_TABLE})      ON (n.name)"),
    ("index_function_name",      f"CREATE INDEX index_function_name      IF NOT EXISTS FOR (n:{LABEL_FUNCTION})   ON (n.name)"),
    ("index_service_name",       f"CREATE INDEX index_service_name       IF NOT EXISTS FOR (n:{LABEL_SERVICE})    ON (n.name)"),
    ("index_api_path",           f"CREATE INDEX index_api_path           IF NOT EXISTS FOR (n:{LABEL_API_ENDPOINT}) ON (n.path)"),
    ("index_workflow_name",      f"CREATE INDEX index_workflow_name      IF NOT EXISTS FOR (n:{LABEL_WORKFLOW})   ON (n.name)"),
    ("index_workflow_scheduler", f"CREATE INDEX index_workflow_scheduler IF NOT EXISTS FOR (n:{LABEL_WORKFLOW})   ON (n.scheduler_type)"),
    ("index_task_name",          f"CREATE INDEX index_task_name          IF NOT EXISTS FOR (n:{LABEL_TASK})       ON (n.name)"),
    ("index_repo_source",        f"CREATE INDEX index_repo_source        IF NOT EXISTS FOR (n:{LABEL_REPOSITORY}) ON (n.source)"),
]


def apply_schema(client: GraphClient) -> None:
    """Idempotently apply constraints and indexes."""
    if not client.available:
        return
    for name, cypher in _CONSTRAINTS:
        try:
            client.run_write(cypher)
        except Exception as exc:
            logger.debug("Constraint %s: %s", name, exc)
    for name, cypher in _INDEXES:
        try:
            client.run_write(cypher)
        except Exception as exc:
            logger.debug("Index %s: %s", name, exc)
    logger.info("Neo4j schema applied")
