// HR sample graph insert (scope-safe, idempotent)
// Run with: docker exec -i neo4j cypher-shell -u neo4j -p changeme < /path/hr_sample_insert.cypher

// Cleanup old sample data
MATCH (n {sample:'HR_V1'}) DETACH DELETE n;

// -----------------------------------------------------------------------------
// Nodes
// -----------------------------------------------------------------------------
UNWIND [
  ['FrontendApp','EmployeePortal','Landscape'],
  ['FrontendApp','AdminPortal','Landscape'],
  ['FrontendApp','ManagerPortal','Landscape'],
  ['BackendDomain','PayrollService','Landscape'],
  ['BackendDomain','EmployeeService','Landscape'],
  ['BackendDomain','AttendanceService','Landscape'],
  ['JobPlatform','JP1_Hitachi','Landscape'],
  ['ExternalSystem','BankGateway','Landscape'],
  ['ExternalSystem','TaxAgency','Landscape'],
  ['ExternalSystem','EmailGateway','Landscape'],
  ['DatabaseCluster','OracleHRCluster','Landscape'],
  ['ApiGateway','HrApiGateway','Contract'],
  ['EventTopic','payroll.generated','Contract'],
  ['EventTopic','employee.updated','Contract'],
  ['Queue','payroll.queue','Contract'],
  ['BatchInterface','JP1_PAYROLL_MONTHLY','Contract'],
  ['FileContract','payslip_csv_v1','Contract'],
  ['FileContract','bank_transfer_txt_v2','Contract'],
  ['JobNet','MonthlyPayrollNet','Orchestration'],
  ['JobNet','SalaryReportNet','Orchestration'],
  ['Job','ExtractAttendanceJob','Orchestration'],
  ['Job','CalcPayrollJob','Orchestration'],
  ['Job','ExportBankFileJob','Orchestration'],
  ['Job','NotifyEmployeeJob','Orchestration'],
  ['ExecutionUnit','DotNetRunner','Orchestration'],
  ['ExecutionUnit','SqlPlusRunner','Orchestration'],
  ['Executable','PayrollBatchRunner.exe','Orchestration'],
  ['SqlPlusScript','payroll_month_end.sql','Orchestration'],
  ['Controller','PayrollController','Execution'],
  ['ServiceClass','PayrollService','Execution'],
  ['Method','GeneratePayslipsAsync','Execution'],
  ['Method','GetPayslipsByPeriodAsync','Execution'],
  ['Package','HR.PKG_PAYROLL','Execution'],
  ['Package','HR.PKG_EMPLOYEE','Execution'],
  ['StoredProcedure','HR.PKG_PAYROLL.open_pay_period','Execution'],
  ['StoredProcedure','HR.PKG_PAYROLL.generate_payslips','Execution'],
  ['Function','HR.PKG_PAYROLL.calc_tax','Execution'],
  ['Trigger','PAYROLL.TRG_PAYSLIP_AUDIT','Execution'],
  ['Query','Q_READ_PAYSLIPS','Execution'],
  ['Query','Q_WRITE_PAYSLIPS','Execution'],
  ['Database','OracleHR','DataLineage'],
  ['Schema','HR','DataLineage'],
  ['Schema','PAYROLL','DataLineage'],
  ['Table','HR.EMPLOYEES','DataLineage'],
  ['Table','PAYROLL.PAY_PERIODS','DataLineage'],
  ['Table','PAYROLL.PAYSLIPS','DataLineage'],
  ['Table','PAYROLL.ALLOWANCES','DataLineage'],
  ['Sequence','PAYROLL.SEQ_PAY_PERIODS','DataLineage'],
  ['Sequence','PAYROLL.SEQ_PAYSLIPS','DataLineage'],
  ['Column','PAYROLL.PAYSLIPS.STATUS','DataLineage'],
  ['Column','PAYROLL.PAYSLIPS.NET_PAY','DataLineage'],
  ['File','payroll_2026_04.csv','DataLineage'],
  ['File','bank_transfer_2026_04.txt','DataLineage'],
  ['Storage','SFTP_BANK_DROP','DataLineage'],
  ['SearchIndex','PayrollSearchIndex','DataLineage'],
  ['Cache','PayrollCache','DataLineage']
] AS row
CALL apoc.merge.node([row[0]], {name: row[1]}, {layer: row[2], sample: 'HR_V1'}, {layer: row[2], sample: 'HR_V1'}) YIELD node
RETURN count(node) AS nodes_merged;

UNWIND [
  ['/api/payroll/generate','POST'],
  ['/api/employees/{id}','GET'],
  ['/api/payroll/payslips','GET']
] AS ep
MERGE (e:ApiEndpoint {path: ep[0], method: ep[1]})
SET e.layer = 'Contract', e.sample = 'HR_V1';

// -----------------------------------------------------------------------------
// Relationships helper pattern:
// MATCH source + MATCH target + MERGE relation
// -----------------------------------------------------------------------------

// Landscape
MATCH (a:FrontendApp {name:'EmployeePortal'}), (b:BackendDomain {name:'EmployeeService'}) MERGE (a)-[:CALLS_DOMAIN]->(b);
MATCH (a:FrontendApp {name:'AdminPortal'}), (b:BackendDomain {name:'EmployeeService'}) MERGE (a)-[:CALLS_DOMAIN]->(b);
MATCH (a:FrontendApp {name:'AdminPortal'}), (b:BackendDomain {name:'PayrollService'}) MERGE (a)-[:CALLS_DOMAIN]->(b);
MATCH (a:FrontendApp {name:'ManagerPortal'}), (b:BackendDomain {name:'AttendanceService'}) MERGE (a)-[:CALLS_DOMAIN]->(b);
MATCH (a:BackendDomain {name:'PayrollService'}), (b:ExternalSystem {name:'BankGateway'}) MERGE (a)-[:INTEGRATES_WITH]->(b);
MATCH (a:BackendDomain {name:'PayrollService'}), (b:ExternalSystem {name:'TaxAgency'}) MERGE (a)-[:INTEGRATES_WITH]->(b);
MATCH (a:BackendDomain {name:'EmployeeService'}), (b:ExternalSystem {name:'EmailGateway'}) MERGE (a)-[:INTEGRATES_WITH]->(b);
MATCH (a:JobPlatform {name:'JP1_Hitachi'}), (b:BackendDomain {name:'PayrollService'}) MERGE (a)-[:SCHEDULES]->(b);
MATCH (a:DatabaseCluster {name:'OracleHRCluster'}), (b:BackendDomain {name:'PayrollService'}) MERGE (a)-[:OWNS_DATA_IN]->(b);
MATCH (a:DatabaseCluster {name:'OracleHRCluster'}), (b:BackendDomain {name:'EmployeeService'}) MERGE (a)-[:OWNS_DATA_IN]->(b);

// Contract
MATCH (a:ApiGateway {name:'HrApiGateway'}), (b:ApiEndpoint {path:'/api/payroll/generate', method:'POST'}) MERGE (a)-[:HAS_ENDPOINT]->(b);
MATCH (a:ApiGateway {name:'HrApiGateway'}), (b:ApiEndpoint {path:'/api/employees/{id}', method:'GET'}) MERGE (a)-[:HAS_ENDPOINT]->(b);
MATCH (a:ApiGateway {name:'HrApiGateway'}), (b:ApiEndpoint {path:'/api/payroll/payslips', method:'GET'}) MERGE (a)-[:HAS_ENDPOINT]->(b);
MATCH (a:BackendDomain {name:'PayrollService'}), (b:ApiEndpoint {path:'/api/payroll/generate', method:'POST'}) MERGE (a)-[:EXPOSES]->(b);
MATCH (a:BackendDomain {name:'PayrollService'}), (b:ApiEndpoint {path:'/api/payroll/payslips', method:'GET'}) MERGE (a)-[:EXPOSES]->(b);
MATCH (a:BackendDomain {name:'EmployeeService'}), (b:ApiEndpoint {path:'/api/employees/{id}', method:'GET'}) MERGE (a)-[:EXPOSES]->(b);
MATCH (a:BackendDomain {name:'PayrollService'}), (b:EventTopic {name:'payroll.generated'}) MERGE (a)-[:PUBLISHES]->(b);
MATCH (a:BackendDomain {name:'EmployeeService'}), (b:EventTopic {name:'employee.updated'}) MERGE (a)-[:PUBLISHES]->(b);
MATCH (a:BackendDomain {name:'AttendanceService'}), (b:EventTopic {name:'employee.updated'}) MERGE (a)-[:CONSUMES]->(b);
MATCH (a:EventTopic {name:'payroll.generated'}), (b:Queue {name:'payroll.queue'}) MERGE (a)-[:ROUTED_TO]->(b);
MATCH (a:BatchInterface {name:'JP1_PAYROLL_MONTHLY'}), (b:ApiEndpoint {path:'/api/payroll/generate', method:'POST'}) MERGE (a)-[:TRIGGERS]->(b);
MATCH (a:BatchInterface {name:'JP1_PAYROLL_MONTHLY'}), (b:FileContract {name:'payslip_csv_v1'}) MERGE (a)-[:EXPORTS_AS]->(b);
MATCH (a:BatchInterface {name:'JP1_PAYROLL_MONTHLY'}), (b:FileContract {name:'bank_transfer_txt_v2'}) MERGE (a)-[:EXPORTS_AS]->(b);

// Orchestration
MATCH (a:JobNet {name:'MonthlyPayrollNet'}), (b:JobNet {name:'SalaryReportNet'}) MERGE (a)-[:CONTAINS]->(b);
MATCH (a:JobNet {name:'MonthlyPayrollNet'}), (b:Job {name:'ExtractAttendanceJob'}) MERGE (a)-[:CONTAINS]->(b);
MATCH (a:JobNet {name:'MonthlyPayrollNet'}), (b:Job {name:'CalcPayrollJob'}) MERGE (a)-[:CONTAINS]->(b);
MATCH (a:JobNet {name:'MonthlyPayrollNet'}), (b:Job {name:'ExportBankFileJob'}) MERGE (a)-[:CONTAINS]->(b);
MATCH (a:JobNet {name:'SalaryReportNet'}), (b:Job {name:'NotifyEmployeeJob'}) MERGE (a)-[:CONTAINS]->(b);
MATCH (a:Job {name:'ExtractAttendanceJob'}), (b:Job {name:'CalcPayrollJob'}) MERGE (a)-[:TRIGGERS]->(b);
MATCH (a:Job {name:'CalcPayrollJob'}), (b:Job {name:'ExportBankFileJob'}) MERGE (a)-[:TRIGGERS]->(b);
MATCH (a:Job {name:'ExportBankFileJob'}), (b:Job {name:'NotifyEmployeeJob'}) MERGE (a)-[:TRIGGERS]->(b);
MATCH (a:Job {name:'CalcPayrollJob'}), (b:ExecutionUnit {name:'DotNetRunner'}) MERGE (a)-[:EXECUTES]->(b);
MATCH (a:Job {name:'ExportBankFileJob'}), (b:ExecutionUnit {name:'SqlPlusRunner'}) MERGE (a)-[:EXECUTES]->(b);
MATCH (a:ExecutionUnit {name:'DotNetRunner'}), (b:Executable {name:'PayrollBatchRunner.exe'}) MERGE (a)-[:RUNS]->(b);
MATCH (a:ExecutionUnit {name:'SqlPlusRunner'}), (b:SqlPlusScript {name:'payroll_month_end.sql'}) MERGE (a)-[:RUNS]->(b);

// Execution
MATCH (a:ApiEndpoint {path:'/api/payroll/generate', method:'POST'}), (b:Controller {name:'PayrollController'}) MERGE (a)-[:HANDLED_BY]->(b);
MATCH (a:ApiEndpoint {path:'/api/payroll/payslips', method:'GET'}), (b:Controller {name:'PayrollController'}) MERGE (a)-[:HANDLED_BY]->(b);
MATCH (a:Controller {name:'PayrollController'}), (b:Method {name:'GeneratePayslipsAsync'}) MERGE (a)-[:CALLS]->(b);
MATCH (a:Controller {name:'PayrollController'}), (b:Method {name:'GetPayslipsByPeriodAsync'}) MERGE (a)-[:CALLS]->(b);
MATCH (a:Method {name:'GeneratePayslipsAsync'}), (b:ServiceClass {name:'PayrollService'}) MERGE (a)-[:EXECUTES_IN]->(b);
MATCH (a:Method {name:'GetPayslipsByPeriodAsync'}), (b:ServiceClass {name:'PayrollService'}) MERGE (a)-[:EXECUTES_IN]->(b);
MATCH (a:Method {name:'GeneratePayslipsAsync'}), (b:StoredProcedure {name:'HR.PKG_PAYROLL.generate_payslips'}) MERGE (a)-[:CALLS_SP]->(b);
MATCH (a:Method {name:'GeneratePayslipsAsync'}), (b:StoredProcedure {name:'HR.PKG_PAYROLL.open_pay_period'}) MERGE (a)-[:CALLS_SP]->(b);
MATCH (a:Method {name:'GetPayslipsByPeriodAsync'}), (b:Query {name:'Q_READ_PAYSLIPS'}) MERGE (a)-[:EXECUTES]->(b);
MATCH (a:Package {name:'HR.PKG_PAYROLL'}), (b:StoredProcedure {name:'HR.PKG_PAYROLL.open_pay_period'}) MERGE (a)-[:DEFINES]->(b);
MATCH (a:Package {name:'HR.PKG_PAYROLL'}), (b:StoredProcedure {name:'HR.PKG_PAYROLL.generate_payslips'}) MERGE (a)-[:DEFINES]->(b);
MATCH (a:Package {name:'HR.PKG_PAYROLL'}), (b:Function {name:'HR.PKG_PAYROLL.calc_tax'}) MERGE (a)-[:DEFINES]->(b);
MATCH (a:StoredProcedure {name:'HR.PKG_PAYROLL.generate_payslips'}), (b:Function {name:'HR.PKG_PAYROLL.calc_tax'}) MERGE (a)-[:CALLS]->(b);
MATCH (a:StoredProcedure {name:'HR.PKG_PAYROLL.generate_payslips'}), (b:Query {name:'Q_WRITE_PAYSLIPS'}) MERGE (a)-[:EXECUTES]->(b);
MATCH (a:SqlPlusScript {name:'payroll_month_end.sql'}), (b:StoredProcedure {name:'HR.PKG_PAYROLL.open_pay_period'}) MERGE (a)-[:CALLS]->(b);
MATCH (a:Trigger {name:'PAYROLL.TRG_PAYSLIP_AUDIT'}), (b:StoredProcedure {name:'HR.PKG_PAYROLL.generate_payslips'}) MERGE (a)-[:EXECUTES]->(b);

// Data lineage
MATCH (a:Database {name:'OracleHR'}), (b:Schema {name:'HR'}) MERGE (a)-[:HAS_SCHEMA]->(b);
MATCH (a:Database {name:'OracleHR'}), (b:Schema {name:'PAYROLL'}) MERGE (a)-[:HAS_SCHEMA]->(b);
MATCH (a:Schema {name:'HR'}), (b:Table {name:'HR.EMPLOYEES'}) MERGE (a)-[:OWNS]->(b);
MATCH (a:Schema {name:'PAYROLL'}), (b:Table {name:'PAYROLL.PAY_PERIODS'}) MERGE (a)-[:OWNS]->(b);
MATCH (a:Schema {name:'PAYROLL'}), (b:Table {name:'PAYROLL.PAYSLIPS'}) MERGE (a)-[:OWNS]->(b);
MATCH (a:Schema {name:'PAYROLL'}), (b:Table {name:'PAYROLL.ALLOWANCES'}) MERGE (a)-[:OWNS]->(b);
MATCH (a:Schema {name:'PAYROLL'}), (b:Sequence {name:'PAYROLL.SEQ_PAY_PERIODS'}) MERGE (a)-[:OWNS]->(b);
MATCH (a:Schema {name:'PAYROLL'}), (b:Sequence {name:'PAYROLL.SEQ_PAYSLIPS'}) MERGE (a)-[:OWNS]->(b);
MATCH (a:Table {name:'PAYROLL.PAYSLIPS'}), (b:Column {name:'PAYROLL.PAYSLIPS.STATUS'}) MERGE (a)-[:HAS_COLUMN]->(b);
MATCH (a:Table {name:'PAYROLL.PAYSLIPS'}), (b:Column {name:'PAYROLL.PAYSLIPS.NET_PAY'}) MERGE (a)-[:HAS_COLUMN]->(b);
MATCH (a:StoredProcedure {name:'HR.PKG_PAYROLL.generate_payslips'}), (b:Table {name:'HR.EMPLOYEES'}) MERGE (a)-[:READS]->(b);
MATCH (a:StoredProcedure {name:'HR.PKG_PAYROLL.generate_payslips'}), (b:Table {name:'PAYROLL.ALLOWANCES'}) MERGE (a)-[:READS]->(b);
MATCH (a:StoredProcedure {name:'HR.PKG_PAYROLL.generate_payslips'}), (b:Table {name:'PAYROLL.PAYSLIPS'}) MERGE (a)-[:WRITES]->(b);
MATCH (a:StoredProcedure {name:'HR.PKG_PAYROLL.open_pay_period'}), (b:Table {name:'PAYROLL.PAY_PERIODS'}) MERGE (a)-[:WRITES]->(b);
MATCH (a:StoredProcedure {name:'HR.PKG_PAYROLL.open_pay_period'}), (b:Sequence {name:'PAYROLL.SEQ_PAY_PERIODS'}) MERGE (a)-[:USES_SEQUENCE]->(b);
MATCH (a:StoredProcedure {name:'HR.PKG_PAYROLL.generate_payslips'}), (b:Sequence {name:'PAYROLL.SEQ_PAYSLIPS'}) MERGE (a)-[:USES_SEQUENCE]->(b);
MATCH (a:Query {name:'Q_READ_PAYSLIPS'}), (b:Table {name:'PAYROLL.PAYSLIPS'}) MERGE (a)-[:READS]->(b);
MATCH (a:Query {name:'Q_READ_PAYSLIPS'}), (b:Table {name:'HR.EMPLOYEES'}) MERGE (a)-[:READS]->(b);
MATCH (a:Query {name:'Q_WRITE_PAYSLIPS'}), (b:Table {name:'PAYROLL.PAYSLIPS'}) MERGE (a)-[:WRITES]->(b);
MATCH (a:Job {name:'ExportBankFileJob'}), (b:File {name:'payroll_2026_04.csv'}) MERGE (a)-[:GENERATES]->(b);
MATCH (a:Job {name:'ExportBankFileJob'}), (b:File {name:'bank_transfer_2026_04.txt'}) MERGE (a)-[:GENERATES]->(b);
MATCH (a:File {name:'payroll_2026_04.csv'}), (b:Storage {name:'SFTP_BANK_DROP'}) MERGE (a)-[:STORED_IN]->(b);
MATCH (a:File {name:'bank_transfer_2026_04.txt'}), (b:Storage {name:'SFTP_BANK_DROP'}) MERGE (a)-[:STORED_IN]->(b);
MATCH (a:File {name:'payroll_2026_04.csv'}), (b:SearchIndex {name:'PayrollSearchIndex'}) MERGE (a)-[:INDEXED_IN]->(b);
MATCH (a:Table {name:'PAYROLL.PAYSLIPS'}), (b:Cache {name:'PayrollCache'}) MERGE (a)-[:CACHED_IN]->(b);
MATCH (a:BackendDomain {name:'PayrollService'}), (b:ExternalSystem {name:'BankGateway'}) MERGE (a)-[:SYNC_TO]->(b);
MATCH (a:BackendDomain {name:'PayrollService'}), (b:ExternalSystem {name:'TaxAgency'}) MERGE (a)-[:SYNC_TO]->(b);

// Metadata for trust layer
MATCH (n {sample:'HR_V1'})
SET n.source_type = coalesce(n.source_type, 'sample'),
    n.extraction_method = coalesce(n.extraction_method, 'manual_script'),
    n.confidence = coalesce(n.confidence, 0.8),
    n.verified = coalesce(n.verified, false),
    n.last_seen_at = datetime();

MATCH (a {sample:'HR_V1'})-[r]->(b {sample:'HR_V1'})
SET r.source_type = coalesce(r.source_type, 'sample'),
    r.extraction_method = coalesce(r.extraction_method, 'manual_script'),
    r.confidence = coalesce(r.confidence, 0.8),
    r.verified = coalesce(r.verified, false),
    r.last_seen_at = datetime();

RETURN 'HR_V1 sample graph inserted (scope-safe)' AS result;
