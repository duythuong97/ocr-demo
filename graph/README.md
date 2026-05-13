# Graph Export — Hướng dẫn sử dụng

Công cụ này đọc `sources.yaml`, chạy extractors trên source code / file CSV / DDL, sinh ra `.cypher` files rồi nạp vào Neo4j.

---

## Mục lục

1. [Workflow tổng quan](#1-workflow-tổng-quan)
2. [Cấu trúc sources.yaml](#2-cấu-trúc-sourcesyaml)
3. [Tầng 1 — Landscape (config-driven)](#3-tầng-1--landscape-config-driven)
4. [Tầng 2 — Project (config-driven)](#4-tầng-2--project-config-driven)
5. [Tầng 3 — Logic (file-driven)](#5-tầng-3--logic-file-driven)
6. [Tầng 4 — Data (file-driven)](#6-tầng-4--data-file-driven)
7. [Edge Types](#7-edge-types)
8. [Defaults & Inheritance](#8-defaults--inheritance)
9. [QName Format](#9-qname-format)

---

## 1. Workflow tổng quan

```
sources.yaml
    │
    ▼
python graph/export.py --config graph/sources.yaml --output graph/exports/
    │  Sinh ra RUN_YYYYMMDD_HHMMSS/
    │    ├── manifest.json
    │    └── repositories/
    │          ├── landscape/_/       ← Tầng 1 nodes
    │          ├── _projects/_/       ← Tầng 2 nodes
    │          └── <repo>/<project>/  ← Tầng 3+4 nodes
    │
    ▼
python graph/load.py --run RUN_YYYYMMDD_HHMMSS
    │  Nạp .cypher vào Neo4j theo thứ tự: nodes → edges_internal → edges_cross
    │
    ▼
python graph/validate.py --run RUN_YYYYMMDD_HHMMSS
```

### CLI đầy đủ

```bash
# Export (sinh .cypher)
python graph/export.py \
  --config graph/sources.yaml \
  --output graph/exports/          # thư mục output, mặc định: graph/exports/
  --repo csharp_hr_repo            # chỉ export 1 repo (tuỳ chọn)
  --project aservice_api           # chỉ export 1 project (tuỳ chọn)
  --dry-run                        # chạy extractors nhưng không ghi file

# Load vào Neo4j
python graph/load.py \
  --run RUN_20260513_150021        # Run ID lấy từ output export
  --output graph/exports/          # phải khớp với export (mặc định: graph/exports/)

# Validate
python graph/validate.py \
  --run RUN_20260513_150021
```

---

## 2. Cấu trúc sources.yaml

```yaml
version: 1

defaults:            # áp dụng cho mọi project, có thể override từng cấp
  system: hr
  owner_team: hr-team
  confidence: { code_ast: 0.95, manual: 0.80, ... }
  shard_size: 10000  # số MERGE tối đa mỗi file .cypher

landscapes:          # Tầng 1 — khai báo là có node ngay
  <node_key>:
    label: <ApiService|Database|FrontendApp|JobPlatform|Storage|ExternalService>
    name: <display name>
    properties:
      <identity_key>: <value>   # bắt buộc, xem bảng Tầng 1
      ...
    uses_db: [...]              # edge fields (xem bảng Edge của từng label)
    calls_api: [...]
    depends_on: [...]

files:               # Manual / static file configs — không cần repo/vcs
  <file_key>:        # repo_key = "files" (synthetic), project_key = file_key
    path: graph/files           # folder chứa file (tương đối từ sources.yaml)
    type: <files|jobs>          # mặc định: "files"
    include_files:              # chỉ dùng với type: files
      - table_list.csv
    metadata:
      <metadata_key>: <value>

repositories:        # Code repos (git/vcs) — Tầng 2+3+4
  <repo_key>:
    path: /absolute/path/to/repo   # hoặc path tương đối từ sources.yaml
    vcs_url: https://...           # tuỳ chọn, lưu vào node
    source: git                    # git | manual | generated
    defaults:                      # override global defaults cho repo này
      stack: csharp
      owner_team: hr-backend-team
    projects:
      <project_key>:
        type: <api|plsql|ddl|angular|library|jobs>
        path: src/MyProject        # sub-path trong repo (tuỳ chọn)
        metadata:
          <metadata_key>: <value>  # xem bảng metadata từng type
        shard_size: 5000           # override shard_size cho project này
```

---

## 3. Tầng 1 — Landscape (config-driven)

Khai báo trong `landscapes:` → node được tạo **tự động**, không cần source code.

### 3.1 ApiService

Đơn vị runtime/deployment có endpoint riêng.

| Property          | Bắt buộc | Mô tả                                            |
| ----------------- | -------- | ------------------------------------------------ |
| `service_id`      | ✅       | Identity key — QName = `ApiService:{service_id}` |
| `name`            |          | Display name                                     |
| `bounded_context` |          | Domain context (employee, payroll, ...)          |
| `expose_channel`  |          | `internal` \| `external` \| `public`             |
| `description`     |          | Mô tả                                            |

**Edge fields (trong YAML):**

| Field                       | Relationship | Target label           |
| --------------------------- | ------------ | ---------------------- |
| `uses_db: [db_key, ...]`    | `USES_DB`    | `Database`             |
| `calls_api: [svc_key, ...]` | `CALLS_API`  | `ApiService`           |
| `depends_on: [key, ...]`    | `DEPENDS_ON` | bất kỳ Landscape label |

```yaml
landscapes:
  aservice:
    label: ApiService
    properties:
      service_id: aservice
      bounded_context: employee
      expose_channel: internal
      description: Employee management API
    uses_db:
      - OracleHRDB # node_key của Database trong cùng landscapes:
```

---

### 3.2 Database

DBMS instance / cluster.

| Property      | Bắt buộc | Mô tả                                        |
| ------------- | -------- | -------------------------------------------- |
| `db_name`     | ✅       | Identity key — QName = `Database:{db_name}`  |
| `db_engine`   |          | `oracle` \| `postgres` \| `mssql` \| `mysql` |
| `host`        |          | Hostname                                     |
| `description` |          | Mô tả                                        |

```yaml
OracleHRDB:
  label: Database
  properties:
    db_name: OracleHRDB
    db_engine: oracle
    description: HR & Employee core database
```

---

### 3.3 FrontendApp

Angular / React / Vue application.

| Property      | Bắt buộc | Mô tả                                         |
| ------------- | -------- | --------------------------------------------- |
| `app_id`      | ✅       | Identity key — QName = `FrontendApp:{app_id}` |
| `framework`   |          | `angular` \| `react` \| `vue`                 |
| `description` |          | Mô tả                                         |

**Edge fields:**

| Field                       | Relationship | Target label |
| --------------------------- | ------------ | ------------ |
| `calls_api: [svc_key, ...]` | `CALLS_API`  | `ApiService` |

```yaml
EmployeePortal:
  label: FrontendApp
  properties:
    app_id: employee_portal
    framework: angular
  calls_api:
    - aservice
```

---

### 3.4 JobPlatform

Hệ thống chạy batch/scheduled jobs (JP1, Airflow, ...).

| Property          | Bắt buộc | Mô tả                                                  |
| ----------------- | -------- | ------------------------------------------------------ |
| `job_platform_id` | ✅       | Identity key — QName = `JobPlatform:{job_platform_id}` |
| `location`        |          | `on-premise` \| `cloud`                                |
| `description`     |          | Mô tả                                                  |

```yaml
JP1JobScheduler:
  label: JobPlatform
  properties:
    job_platform_id: JP1
    location: on-premise
```

---

### 3.5 Storage

S3, NAS, NFS, Blob storage.

| Property       | Bắt buộc | Mô tả                                         |
| -------------- | -------- | --------------------------------------------- |
| `storage_id`   | ✅       | Identity key — QName = `Storage:{storage_id}` |
| `storage_type` |          | `s3` \| `nfs` \| `blob` \| `local`            |
| `description`  |          | Mô tả                                         |

```yaml
S3DataLake:
  label: Storage
  properties:
    storage_id: s3-data-lake
    storage_type: s3
```

---

### 3.6 ExternalService

Hệ thống bên ngoài (SSO, Payment, ...).

| Property       | Bắt buộc | Mô tả                                                 |
| -------------- | -------- | ----------------------------------------------------- |
| `service_id`   | ✅       | Identity key — QName = `ExternalService:{service_id}` |
| `service_type` |          | `sso` \| `payment` \| `email` \| `storage`            |
| `endpoint`     |          | URL endpoint                                          |
| `description`  |          | Mô tả                                                 |

```yaml
OktaSSO:
  label: ExternalService
  properties:
    service_id: okta_sso
    service_type: sso
    endpoint: https://idp.okta.com
```

---

## 4. Tầng 2 — Project (config-driven)

Mỗi entry trong `files:` hoặc `repositories: > projects:` → một `Project` node được tạo **tự động** bởi `project_synthesizer`, không cần file nào.

**QName:** `Project:{repo_key}:{project_key}` — với `files:` section thì `repo_key = "files"`

**Edge tự động:**
Nếu `metadata` chứa `service_id` / `app_id` / `job_platform_id` / `db_name` → tự tạo `(Landscape)-[:CONTAINS]->(Project)`.

### Các `type` hỗ trợ

#### `type: api` — C# / Java REST API

**Required metadata:** `service_id`, `bounded_context`, `base_path`

| Metadata           | Bắt buộc | Mô tả                                          | Dùng bởi      |
| ------------------ | -------- | ---------------------------------------------- | ------------- |
| `service_id`       | ✅       | Link tới `ApiService` trong landscape          | pipeline      |
| `bounded_context`  | ✅       | Domain context                                 | node property |
| `base_path`        | ✅       | HTTP base path (e.g. `/api/employee`)          | node property |
| `namespace_prefix` |          | C# namespace prefix (e.g. `HrSystem.AService`) | `csharp.py`   |
| `expose_channel`   |          | `internal` \| `external`                       | node property |
| `stack`            |          | `csharp` \| `java` \| `python`                 | node property |

```yaml
aservice_api:
  type: api
  path: src/AService
  metadata:
    service_id: aservice
    bounded_context: employee
    base_path: /api/employee
    namespace_prefix: HrSystem.AService
```

---

#### `type: plsql` — Oracle PL/SQL source set

**Required metadata:** `schema`, `db_name`

Scan toàn bộ `path/` và tự nhận file theo extension: `.pks`, `.pkb`, `.pck`, `.pls`, `.plb`, `.fnc`, `.prc`, `.trg`, và **`.sql` nếu trong file có `CREATE OR REPLACE PACKAGE/PROCEDURE/FUNCTION/TRIGGER`**.

| Metadata          | Bắt buộc | Mô tả                                                                | Dùng bởi                         |
| ----------------- | -------- | -------------------------------------------------------------------- | -------------------------------- |
| `schema`          | ✅       | Oracle schema (e.g. `HR`) — fallback khi code không có schema prefix | `oracle_plsql.py`                |
| `db_name`         | ✅       | Link tới `Database` trong landscape                                  | `oracle_plsql.py`, node property |
| `db_engine`       |          | `oracle`                                                             | node property                    |
| `bounded_context` |          | Domain context                                                       | node property                    |

```yaml
hr_schema_objects:
  type: plsql
  path: plsql # scan plsql/** — nhận cả .sql chứa PL/SQL
  metadata:
    schema: HR
    db_name: OracleHRDB
    db_engine: oracle
    bounded_context: employee
```

---

#### `type: angular` — Angular application

**Required metadata:** `app_id`

| Metadata               | Bắt buộc | Mô tả                                       | Dùng bởi      |
| ---------------------- | -------- | ------------------------------------------- | ------------- |
| `app_id`               | ✅       | Link tới `FrontendApp` trong landscape      | pipeline      |
| `route_prefix`         |          | HTTP route prefix (e.g. `/portal/employee`) | node property |
| `consumes_service_ids` |          | List ApiService được gọi                    | node property |

```yaml
employee_portal:
  type: angular
  path: projects/employee-portal
  metadata:
    app_id: employee_portal
    route_prefix: /portal/employee
    consumes_service_ids:
      - aservice
```

---

#### `type: library` — Shared library (không thuộc Landscape nào)

**Required metadata:** không có

| Metadata          | Bắt buộc | Mô tả            |
| ----------------- | -------- | ---------------- |
| `bounded_context` |          | Domain context   |
| `stack`           |          | Technology stack |

```yaml
shared_lib:
  type: library
  path: src/Shared
  metadata:
    bounded_context: shared
```

---

#### `type: files` — File list tường minh (CSV, manual)

**Required metadata:** không có — dùng `include_files:` để chỉ định file cụ thể.

| Metadata        | Bắt buộc | Mô tả                                                        | Dùng bởi            |
| --------------- | -------- | ------------------------------------------------------------ | ------------------- |
| `source_type`   |          | `manual` \| `generated`                                      | node property       |
| `confidence`    |          | Override confidence score                                    | pipeline            |
| `schema_db_map` |          | `{SCHEMA: db_name}` — dùng khi CSV không có `db_name` column | `csv_repository.py` |

```yaml
table_inventory:
  type: files
  include_files:
    - table_list.csv
  metadata:
    source_type: manual
    confidence: 0.8

repository_mapping:
  type: files
  include_files:
    - repository_list.csv
  metadata:
    source_type: manual
    schema_db_map:
      HR: OracleHRDB
      PAYROLL: OraclePayrollDB
```

---

#### `type: jobs` — Job manifest files

**Required metadata:** không có (nhưng nên có `job_platform_id`)

| Metadata          | Bắt buộc | Mô tả                                  |
| ----------------- | -------- | -------------------------------------- |
| `job_platform_id` |          | Link tới `JobPlatform` trong landscape |
| `bounded_context` |          | Domain context                         |

```yaml
jp1_payroll_jobs:
  type: jobs
  path: jp1/payroll
  metadata:
    job_platform_id: JP1
    bounded_context: payroll
  include_files:
    - payroll_calc_job.yaml
```

---

## 5. Tầng 3 — Logic (file-driven)

Node được tạo bởi **extractor** khi scan source code. Cần khai báo `path:` trỏ đến thư mục chứa files.

### Extractor map

| Extractor           | File patterns                                                   | Node labels tạo ra                                                | Edge labels tạo ra                                                                             |
| ------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `oracle_plsql.py`   | `*.pks`, `*.pkb`, `*.pck`, `*.fnc`, `*.prc`, `*.trg`, `*.sql`\* | `Class` (package), `Function` (proc/func/trigger), `Table` (stub) | `BELONGS_TO`, `READS_FROM`, `WRITES_TO`, `INSERTS_INTO`, `UPDATES`, `DELETES_FROM`, `TRIGGERS` |
| `oracle_ddl.py`     | `*.sql`, `*.ddl`\*                                              | `Table` (với đầy đủ column info)                                  | `CONTAINS` (Database→Table)                                                                    |
| `yaml_jobs.py`      | `*.yaml`, `*.yml`\*                                             | `Job`, `Function` (stub), `File` (stub)                           | `CONTAINS` (JobPlatform→Job), `CALLS`, `EXECUTES`                                              |
| `csv_table.py`      | `*.csv`\*                                                       | `Table`                                                           | `CONTAINS` (Database→Table)                                                                    |
| `csv_repository.py` | `*.csv`\*                                                       | `ServiceClass`, `Table` (stub)                                    | `READS_FROM`, `INSERTS_INTO`, `UPDATES`, `DELETES_FROM`, `WRITES_TO`                           |
| `csharp.py`         | `*.cs`                                                          | `Class`, `Function`, `Service`                                    | `READS_FROM`, `WRITES_TO`, `BELONGS_TO`                                                        |
| `angular_module.py` | `*.module.ts`                                                   | `Module`, `FrontendComponent`                                     | `DEPENDS_ON`, `CONTAINS`                                                                       |
| `typescript.py`     | `*.ts`, `*.tsx`                                                 | `FrontendComponent`, `Service`                                    | `CALLS_API`                                                                                    |

\*`can_handle()` kiểm tra thêm nội dung file — không phải mọi file cùng extension đều được xử lý.

> **Note:** `oracle_plsql.py` và `csharp.py` hiện vẫn emit label `Class`/`Function`/`Service` (legacy). Sẽ migrate sang `PLSQLPackage`/`Procedure`/`ApiController` ở version sau.
> **`.sql` files:** `oracle_plsql.py` nhận `.sql` nếu chứa `CREATE OR REPLACE PACKAGE/PROCEDURE/FUNCTION/TRIGGER`; `oracle_ddl.py` nhận `.sql` nếu chứa `CREATE TABLE/VIEW`. Cả hai có thể cùng scan một folder — mỗi file chỉ được xử lý bởi extractor phù hợp.

### Ví dụ: PL/SQL packages

```yaml
repositories:
  oracle_hr_repo:
    path: /repos/plsql-hr
    projects:
      hr_schema_objects:
        type: plsql
        path: HR # scan /repos/plsql-hr/HR/**
        metadata:
          schema: HR
          db_name: OracleHRDB
```

Extractor sẽ tìm và parse:

```
HR/
  pkg_employee.pks   → Class node: Class:oracle_hr_repo:PKG_EMPLOYEE
  pkg_employee.pkb   → Function nodes: Function:...:GET_EMPLOYEE, UPDATE_SALARY, ...
  fnc_get_budget.fnc → Function node độc lập
```

### Ví dụ: C# API

```yaml
aservice_api:
  type: api
  path: src/AService
  metadata:
    service_id: aservice
    namespace_prefix: HrSystem.AService
```

Extractor parse `*.cs` → tạo `Class` (controller, service, repo) + `READS_FROM` / `WRITES_TO` edges tới `Table` từ SQL string literals.

---

## 6. Tầng 4 — Data (file-driven)

### 6.1 Table từ CSV inventory (`table_list.csv`)

**Extractor:** `CsvTableExtractor`
**Trigger:** file `.csv` có header chứa cột `scheme` và `name`

**Cột CSV:**

| Cột           | Bắt buộc | Mô tả                                             |
| ------------- | -------- | ------------------------------------------------- | --- |
| `scheme`      | ✅       | Oracle schema (e.g. `HR`)                         | ◊   |
| `name`        | ✅       | Table name (e.g. `EMPLOYEES`)                     |
| `db_name`     |          | Database owner — nếu rỗng, dùng `context.db_name` |
| `description` |          | Mô tả                                             |

**Node tạo ra:** `Table:{db_name}:{scheme}.{name}`
**Edge tạo ra:** `(Database:{db_name})-[:CONTAINS]->(Table:...)`

```csv
scheme,name,db_name,description
HR,EMPLOYEES,OracleHRDB,Main employee master table
HR,DEPARTMENTS,OracleHRDB,Department reference
PAYROLL,SALARY_RUNS,OraclePayrollDB,Monthly payroll run header
```

---

### 6.2 Table stubs từ repository mapping (`repository_list.csv`)

**Extractor:** `CsvRepositoryExtractor`
**Trigger:** file `.csv` có header chứa cột `class_name` và `table`

**Cột CSV:**

| Cột               | Bắt buộc | Mô tả                                                         |
| ----------------- | -------- | ------------------------------------------------------------- |
| `class_name`      | ✅       | Repository class name (e.g. `EmployeeRepository`)             |
| `table`           | ✅       | Table name                                                    |
| `schema`          |          | Oracle schema — dùng với `schema_db_map` để resolve `db_name` |
| `service_id`      |          | Dùng trong QName của ServiceClass node                        |
| `bounded_context` |          | Domain context                                                |
| `operation`       |          | `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CALL`       |
| `description`     |          | Mô tả                                                         |

**Node tạo ra:**

- `ServiceClass:{repo_key}:{service_id}:{bounded_context}:{class_name}`
- `Table:{db_name}:{schema}.{table}` (stub, merge với node từ DDL/CSV)

**Edge tạo ra:** `READS_FROM` (SELECT) hoặc `WRITES_TO` (INSERT/UPDATE/DELETE/MERGE)

**Metadata `schema_db_map`** trong sources.yaml dùng để map schema → db_name khi CSV không có cột `db_name`:

```yaml
metadata:
  schema_db_map:
    HR: OracleHRDB
    PAYROLL: OraclePayrollDB
    ATTENDANCE: OracleAttendanceDB
```

---

### 6.3 Table + Column từ Oracle DDL

**Extractor:** `OracleDdlExtractor`
**File patterns:** `*.sql`, `*.ddl` (auto-detect bằng `can_handle()`)

Parse `CREATE TABLE`, `ALTER TABLE ADD`, `CREATE VIEW` → tạo `Table` node với đầy đủ column metadata (name, type, nullable, pk_columns). Schema được extract trực tiếp từ DDL (`CREATE TABLE HR.EMPLOYEES` → schema=`HR`). Dùng `type: ddl` để scan cả folder:

```yaml
hr_ddl:
  type: ddl # scan toàn bộ path/ddl/**
  path: ddl
  metadata:
    db_name: OracleHRDB # fallback khi DDL không có schema prefix
    db_engine: oracle
    schema_db_map: # map schema → db_name khác nhau trong cùng folder
      HR: OracleHRDB
      PAYROLL: OraclePayrollDB
```

Nếu folder DDL chứa nhiều schema với DB riêng, dùng `schema_db_map` để gán đúng `db_name` cho mỗi schema.

---

## 7. Edge Types

### Edges từ Landscape config (sources.yaml)

| YAML field          | Relationship | From → To                                   |
| ------------------- | ------------ | ------------------------------------------- |
| `uses_db: [...]`    | `USES_DB`    | `ApiService` → `Database`                   |
| `calls_api: [...]`  | `CALLS_API`  | `ApiService` / `FrontendApp` → `ApiService` |
| `depends_on: [...]` | `DEPENDS_ON` | bất kỳ → bất kỳ Landscape                   |

### Edges sinh tự động (pipeline)

| Relationship | From → To               | Sinh bởi                                  |
| ------------ | ----------------------- | ----------------------------------------- |
| `CONTAINS`   | `Landscape` → `Project` | `project_synthesizer`                     |
| `CONTAINS`   | `Database` → `Table`    | `CsvTableExtractor`, `OracleDdlExtractor` |
| `CONTAINS`   | `JobPlatform` → `Job`   | `YamlJobsExtractor`                       |

### Edges từ extractors

| Relationship   | From → To                                   | Extractor                                           |
| -------------- | ------------------------------------------- | --------------------------------------------------- |
| `READS_FROM`   | `Function`/`Class`/`ServiceClass` → `Table` | `oracle_plsql.py`, `csharp.py`, `csv_repository.py` |
| `WRITES_TO`    | `Function`/`Class`/`ServiceClass` → `Table` | `oracle_plsql.py`, `csharp.py`, `csv_repository.py` |
| `INSERTS_INTO` | `Function`/`Class` → `Table`                | `oracle_plsql.py`                                   |
| `UPDATES`      | `Function`/`Class` → `Table`                | `oracle_plsql.py`                                   |
| `DELETES_FROM` | `Function`/`Class` → `Table`                | `oracle_plsql.py`                                   |
| `TRUNCATES`    | `Function`/`Class` → `Table`                | `oracle_plsql.py`                                   |
| `BELONGS_TO`   | `Function` → `Class`                        | `oracle_plsql.py`, `csharp.py`                      |
| `TRIGGERS`     | `Function` (trigger) → `Table`              | `oracle_plsql.py`                                   |
| `DEPENDS_ON`   | `Module` → `Module`                         | `angular_module.py`                                 |
| `CONTAINS`     | `Module` → `FrontendComponent`              | `angular_module.py`                                 |
| `CALLS`        | `Function`/`Class` → `Function`/`Procedure` | `oracle_plsql.py`                                   |
| `CALLS`        | `Job` → `Function` (PL/SQL proc)            | `yaml_jobs.py`                                      |
| `EXECUTES`     | `Job` → `File` (sqlplus script / exe)       | `yaml_jobs.py`                                      |

---

## 8. Defaults & Inheritance

Defaults được **merge đệ quy** từ ít ưu tiên → nhiều ưu tiên:

```
global defaults  <  repo defaults  <  project metadata
```

```yaml
defaults: # global
  system: hr
  owner_team: hr-team
  shard_size: 10000

repositories:
  csharp_hr_repo:
    defaults: # repo level — override global
      stack: csharp
      owner_team: hr-backend-team
    projects:
      aservice_api:
        metadata: # project level — override repo
          service_id: aservice
          owner_team: aservice-team # override repo default
```

**Properties propagated vào ExtractionContext:**

| Metadata key       | Dùng trong ExtractionContext              | Dùng bởi                             |
| ------------------ | ----------------------------------------- | ------------------------------------ |
| `service_id`       | `context.service_name`                    | node QName                           |
| `bounded_context`  | `context.domain`                          | node property                        |
| `namespace_prefix` | `context.namespace_prefix`                | `csharp.py`                          |
| `db_name`          | `context.db_name`                         | `oracle_plsql.py`, table QName       |
| `schema`           | `context.extra_tags["schema"]`            | `oracle_plsql.py`                    |
| `stack`            | `context.extra_tags["stack"]`             | node property                        |
| `system`           | `context.extra_tags["system"]`            | node property                        |
| `owner_team`       | `context.repo_owner`, `context.team_name` | node property                        |
| `schema_db_map`    | `context.extra_tags["schema_db_map"]`     | `csv_repository.py`, `oracle_ddl.py` |
| `plsql_repository` | `context.extra_tags["plsql_repository"]`  | `yaml_jobs.py`                       |
| `job_platform_id`  | `context.extra_tags["job_platform_id"]`   | `yaml_jobs.py`                       |

---

## 9. QName Format

`qualified_name` là identity key duy nhất của mỗi node trong Neo4j. Hai node cùng QName = cùng entity (MERGE).

| Label               | QName format                                                  | Ví dụ                                                         |
| ------------------- | ------------------------------------------------------------- | ------------------------------------------------------------- |
| `ApiService`        | `ApiService:{service_id}`                                     | `ApiService:aservice`                                         |
| `Database`          | `Database:{db_name}`                                          | `Database:OracleHRDB`                                         |
| `FrontendApp`       | `FrontendApp:{app_id}`                                        | `FrontendApp:employee_portal`                                 |
| `JobPlatform`       | `JobPlatform:{job_platform_id}`                               | `JobPlatform:JP1`                                             |
| `Storage`           | `Storage:{storage_id}`                                        | `Storage:s3-data-lake`                                        |
| `ExternalService`   | `ExternalService:{service_id}`                                | `ExternalService:okta_sso`                                    |
| `Project`           | `Project:{repo_key}:{project_key}`                            | `Project:csharp_hr_repo:aservice_api`                         |
| `Module`            | `Module:{repo_key}:{module_name}`                             | `Module:angular_portal_repo:EmployeeModule`                   |
| `Class`             | `Class:{repo_key}:{service_id}:{class_name}`                  | `Class:csharp_hr_repo:aservice:EmployeeController`            |
| `Function`          | `Function:{repo_key}:{PKG_NAME}.{PROC_NAME}`                  | `Function:plsql_hr_repo:PKG_EMPLOYEE.GET_EMPLOYEE`            |
| `Job`               | `Job:{job_platform_id}:{JOB_NAME}`                            | `Job:JP1:PAYROLL_CALC_JOB`                                    |
| `ServiceClass`      | `ServiceClass:{repo_key}:{service_id}:{context}:{class_name}` | `ServiceClass:files:aservice:employee:EmployeeRepository`     |
| `Table`             | `Table:{db_name}:{schema}.{name}`                             | `Table:OracleHRDB:HR.EMPLOYEES`                               |
| `FrontendComponent` | `FrontendComponent:{repo_key}:{component_name}`               | `FrontendComponent:angular_portal_repo:EmployeeListComponent` |

> **Quy tắc quan trọng:** `Table` QName **không** chứa `repo_key` — cùng table vật lý từ nhiều repo sẽ MERGE về 1 node duy nhất trong Neo4j.
>
> **`files:` section:** `repo_key` luôn là `"files"` — e.g. `Project:files:table_inventory`, `ServiceClass:files:aservice:employee:EmployeeRepository`.

---

# Node Types (Label) by Layer

## Tầng Landscape

| Label               | Ý nghĩa / Áp dụng thực tế                                                                | Edge Types (as source)                        |
| ------------------- | ---------------------------------------------------------------------------------------- | --------------------------------------------- |
| **ApiService**      | Runtime service/deployment unit (đơn vị deploy/chạy thực tế, có endpoint/cấu hình riêng) | CONTAINS, DEPENDS_ON, CALLS_EXTERNAL, USES_DB |
| **FrontendApp**     | Mỗi Angular app (project)                                                                | CONTAINS                                      |
| **Database**        | Mỗi DBMS instance/cluster                                                                | CONTAINS                                      |
| **JobPlatform**     | Hệ thống chạy job (Airflow, JP1, ...)                                                    | CONTAINS                                      |
| **Storage**         | Nơi lưu artifact (S3, NAS, NFS, Blob)                                                    | CONTAINS                                      |
| **ExternalService** | Hệ thống ngoài (SSO, Payment, ...)                                                       | (thường chỉ là target)                        |

## Tầng Project/Module

| Label       | Ý nghĩa / Áp dụng thực tế                                                             | Edge Types (as source)        |
| ----------- | ------------------------------------------------------------------------------------- | ----------------------------- |
| **Project** | Code artifact/build unit trong repo (csproj, Angular workspace/app, PLSQL source set) | CONTAINS                      |
| **Module**  | C# namespace, Angular module, PL/SQL package                                          | CONTAINS, DEPENDS_ON, IMPORTS |

## Tầng Logic

| Label                 | Ý nghĩa / Áp dụng thực tế                              | Edge Types (as source)                                                                                          |
| --------------------- | ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------- |
| **ApiEndpoint**       | API endpoint (OpenAPI, Controller action)              | HANDLED_BY                                                                                                      |
| **ApiController**     | Lớp nhận HTTP request, map từ endpoint vào luồng xử lý | CONTAINS, DEPENDS_ON, CALLS, CALLS_EXTERNAL                                                                     |
| **ServiceClass**      | Lớp service nghiệp vụ (business logic)                 | CONTAINS, DEPENDS_ON, CALLS, CALLS_EXTERNAL, USES_REPOSITORY                                                    |
| **RepositoryClass**   | Repository class (C#, Java, ...)                       | READS_FROM, WRITES_TO, INSERTS_INTO, UPDATES, DELETES_FROM, TRUNCATES                                           |
| **PLSQLPackage**      | PL/SQL package (header/body)                           | CONTAINS, READS_FROM, WRITES_TO, INSERTS_INTO, UPDATES, DELETES_FROM, TRUNCATES                                 |
| **Procedure**         | PL/SQL stored procedure                                | CALLS, READS_FROM, WRITES_TO, INSERTS_INTO, UPDATES, DELETES_FROM, TRUNCATES, BELONGS_TO                        |
| **SQLFunction**       | PL/SQL function                                        | CALLS, READS_FROM, WRITES_TO, INSERTS_INTO, UPDATES, DELETES_FROM, TRUNCATES, BELONGS_TO, REFERENCES, PUBLISHES |
| **Trigger**           | PL/SQL trigger                                         | TRIGGERS                                                                                                        |
| **Job**               | Batch job, cronjob                                     | EXECUTES, USES_DB                                                                                               |
| **FrontendComponent** | Angular component                                      | CALLS_API                                                                                                       |
| **EventTopic**        | Message/event topic                                    | (thường chỉ là target)                                                                                          |

## Tầng Data

| Label         | Ý nghĩa / Áp dụng thực tế          | Edge Types (as source)          |
| ------------- | ---------------------------------- | ------------------------------- |
| **Table**     | DB table                           | DEFINED_BY, REFERENCES          |
| **Directory** | Thư mục logic/vật lý trong storage | CONTAINS, STORED_IN, REFERENCES |
| **File**      | File input/output                  | STORED_IN, REFERENCES           |
| **Document**  | Tài liệu, resource                 | STORED_IN, REFERENCES           |

---

# Edge Types (Relationship)

| Edge Type           | Source → Target (label)                                                  | Ý nghĩa / Extractor / Rule ví dụ                                   |
| ------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------ |
| **CONTAINS**        | System/Project/Module/Storage/Directory → Project/Module/Class/...       | Tổ chức hierarchy cha-con và cây thư mục                           |
| **DEPENDS_ON**      | Service/Module → Service/Module/ExternalService                          | Service/module phụ thuộc service/module khác                       |
| **CALLS_API**       | FrontendComponent/ApiController/ServiceClass → ApiEndpoint/ApiService    | Gọi API nội bộ/hệ thống ngoài                                      |
| **CALLS_EXTERNAL**  | ApiController/ServiceClass/Procedure/SQLFunction → ExternalService       | Gọi hệ thống ngoài (HTTP, SOAP, ...)                               |
| **CALLS**           | ApiController/ServiceClass/Procedure/SQLFunction → Procedure/SQLFunction | Gọi hàm, proc, method khác                                         |
| **USES_REPOSITORY** | ServiceClass → RepositoryClass                                           | Service nghiệp vụ sử dụng repository                               |
| **HAS_MAPPER**      | RepositoryClass → Module                                                 | Repository dùng XML Mapper                                         |
| **BELONGS_TO**      | SQLFunction/Procedure → Class/PLSQLPackage                               | Hàm/proc thuộc class/package                                       |
| **EXECUTES**        | Job → SQLFunction/Procedure                                              | Job thực thi hàm/proc                                              |
| **TRIGGERS**        | Trigger → Table                                                          | Trigger gắn với bảng                                               |
| **READS_FROM**      | SQLFunction/Procedure/ServiceClass/RepositoryClass/PLSQLPackage → Table  | Đọc dữ liệu từ bảng                                                |
| **WRITES_TO**       | SQLFunction/Procedure/ServiceClass/RepositoryClass/PLSQLPackage → Table  | Edge tổng hợp cho mọi thao tác ghi (INSERT/UPDATE/DELETE/TRUNCATE) |
| **INSERTS_INTO**    | SQLFunction/Procedure/RepositoryClass/PLSQLPackage → Table               | Thao tác INSERT vào bảng                                           |
| **UPDATES**         | SQLFunction/Procedure/RepositoryClass/PLSQLPackage → Table               | Thao tác UPDATE trên bảng                                          |
| **DELETES_FROM**    | SQLFunction/Procedure/RepositoryClass/PLSQLPackage → Table               | Thao tác DELETE FROM bảng                                          |
| **TRUNCATES**       | SQLFunction/Procedure/RepositoryClass/PLSQLPackage → Table               | Thao tác TRUNCATE TABLE                                            |
| **REFERENCES**      | SQLFunction/Procedure/Directory → File/Document                          | Tham chiếu file, tài liệu                                          |
| **HANDLED_BY**      | ApiEndpoint → ApiController/ServiceClass                                 | API endpoint được xử lý bởi controller/service                     |
| **HAS_ENDPOINT**    | ApiGateway → ApiEndpoint                                                 | API gateway chứa endpoint                                          |
| **PUBLISHES**       | SQLFunction/ServiceClass → EventTopic                                    | Publish event/message                                              |
| **SUBSCRIBES**      | Class → EventTopic                                                       | Subscribe event/message                                            |
| **IMPORTS**         | Module/Class → Module/Class                                              | Import module/class khác                                           |
| **DEFINED_BY**      | Table → Document                                                         | Định nghĩa bởi tài liệu                                            |
| **DOCUMENTED_BY**   | SQLFunction/Procedure/ServiceClass → Document                            | Được document bởi tài liệu                                         |
| **HOSTS**           | GitRepository → Service                                                  | Git repo chứa service                                              |
| **PROVIDES_API**    | Domain → ApiEndpoint                                                     | Domain cung cấp API                                                |
| **INSTANTIATES**    | ServiceClass/ApiController → Class                                       | Hàm khởi tạo class                                                 |
| **USES_DB**         | Service/Job → Database                                                   | Service/job sử dụng database                                       |
| **STORES_FILE_IN**  | Service → Storage                                                        | Service ghi file vào storage                                       |
| **STORED_IN**       | Directory/File/Document → Storage/Directory                              | Artifact được lưu trực tiếp trong storage hoặc theo cây thư mục    |
