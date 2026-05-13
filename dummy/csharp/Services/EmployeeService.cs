using System;
using System.Collections.Generic;
using System.Data;
using System.Threading.Tasks;
using Microsoft.Data.SqlClient;
using HRSystem.Models;

namespace HRSystem.Services
{
    public class EmployeeService
    {
        private readonly string _connStr;

        public EmployeeService(string connectionString)
        {
            _connStr = connectionString;
        }

        public async Task<Employee> GetByIdAsync(int empId)
        {
            const string sql = @"
                SELECT e.EMP_ID, e.FIRST_NAME, e.LAST_NAME, e.EMAIL,
                       e.SALARY, e.STATUS, e.HIRE_DATE,
                       d.DEPT_NAME, j.JOB_TITLE
                FROM EMPLOYEES e
                JOIN DEPARTMENTS d ON e.DEPT_ID = d.DEPT_ID
                JOIN JOBS j ON e.JOB_ID = j.JOB_ID
                WHERE e.EMP_ID = @empId AND e.STATUS = 'ACTIVE'";

            using var conn = new SqlConnection(_connStr);
            using var cmd  = new SqlCommand(sql, conn);
            cmd.Parameters.AddWithValue("@empId", empId);
            await conn.OpenAsync();

            using var reader = await cmd.ExecuteReaderAsync();
            if (!await reader.ReadAsync()) return null;

            return MapEmployee(reader);
        }

        public async Task<List<Employee>> SearchAsync(string keyword, int? deptId = null)
        {
            var sql = @"
                SELECT e.EMP_ID, e.FIRST_NAME, e.LAST_NAME, e.EMAIL,
                       e.SALARY, e.STATUS, e.HIRE_DATE,
                       d.DEPT_NAME, j.JOB_TITLE
                FROM EMPLOYEES e
                JOIN DEPARTMENTS d ON e.DEPT_ID = d.DEPT_ID
                JOIN JOBS j ON e.JOB_ID = j.JOB_ID
                WHERE e.STATUS = 'ACTIVE'
                  AND (e.FIRST_NAME LIKE @kw OR e.LAST_NAME LIKE @kw OR e.EMAIL LIKE @kw)";

            if (deptId.HasValue)
                sql += " AND e.DEPT_ID = @deptId";

            sql += " ORDER BY e.LAST_NAME, e.FIRST_NAME";

            using var conn = new SqlConnection(_connStr);
            using var cmd  = new SqlCommand(sql, conn);
            cmd.Parameters.AddWithValue("@kw", $"%{keyword}%");
            if (deptId.HasValue) cmd.Parameters.AddWithValue("@deptId", deptId.Value);
            await conn.OpenAsync();

            var list   = new List<Employee>();
            using var reader = await cmd.ExecuteReaderAsync();
            while (await reader.ReadAsync())
                list.Add(MapEmployee(reader));

            return list;
        }

        public async Task UpdateSalaryAsync(int empId, decimal newSalary, string reason)
        {
            const string selectSql = @"
                SELECT SALARY FROM EMPLOYEES WHERE EMP_ID = @empId AND STATUS = 'ACTIVE'";

            const string updateSql = @"
                UPDATE EMPLOYEES
                SET SALARY = @newSalary, UPDATED_AT = GETDATE()
                WHERE EMP_ID = @empId AND STATUS = 'ACTIVE'";

            const string historySql = @"
                INSERT INTO SALARY_HISTORY (EMP_ID, OLD_SALARY, NEW_SALARY, CHANGE_DATE, REASON)
                VALUES (@empId, @oldSalary, @newSalary, GETDATE(), @reason)";

            using var conn = new SqlConnection(_connStr);
            await conn.OpenAsync();
            using var tx = conn.BeginTransaction();

            var cmd = new SqlCommand(selectSql, conn, tx);
            cmd.Parameters.AddWithValue("@empId", empId);
            var oldSalary = (decimal)await cmd.ExecuteScalarAsync();

            cmd = new SqlCommand(updateSql, conn, tx);
            cmd.Parameters.AddWithValue("@newSalary", newSalary);
            cmd.Parameters.AddWithValue("@empId", empId);
            await cmd.ExecuteNonQueryAsync();

            cmd = new SqlCommand(historySql, conn, tx);
            cmd.Parameters.AddWithValue("@empId", empId);
            cmd.Parameters.AddWithValue("@oldSalary", oldSalary);
            cmd.Parameters.AddWithValue("@newSalary", newSalary);
            cmd.Parameters.AddWithValue("@reason", reason ?? (object)DBNull.Value);
            await cmd.ExecuteNonQueryAsync();

            tx.Commit();
        }

        public async Task<bool> TerminateAsync(int empId)
        {
            const string sql = @"
                UPDATE EMPLOYEES
                SET STATUS = 'TERMINATED', UPDATED_AT = GETDATE()
                WHERE EMP_ID = @empId AND STATUS = 'ACTIVE';

                UPDATE LEAVE_REQUESTS
                SET STATUS = 'CANCELLED'
                WHERE EMP_ID = @empId AND STATUS = 'PENDING';";

            using var conn = new SqlConnection(_connStr);
            await conn.OpenAsync();
            using var cmd = new SqlCommand(sql, conn);
            cmd.Parameters.AddWithValue("@empId", empId);
            int rows = await cmd.ExecuteNonQueryAsync();
            return rows > 0;
        }

        public async Task<List<AuditEntry>> GetAuditHistoryAsync(int empId)
        {
            const string sql = @"
                SELECT LOG_ID, TABLE_NAME, OPERATION, OLD_VALUES, NEW_VALUES, CHANGED_BY, CHANGED_AT
                FROM AUDIT_LOG
                WHERE TABLE_NAME = 'EMPLOYEES' AND RECORD_ID = @empId
                ORDER BY CHANGED_AT DESC";

            using var conn = new SqlConnection(_connStr);
            using var cmd  = new SqlCommand(sql, conn);
            cmd.Parameters.AddWithValue("@empId", empId);
            await conn.OpenAsync();

            var list   = new List<AuditEntry>();
            using var reader = await cmd.ExecuteReaderAsync();
            while (await reader.ReadAsync())
                list.Add(new AuditEntry { /* map fields */ });

            return list;
        }

        private static Employee MapEmployee(IDataReader r) => new Employee
        {
            EmpId     = r.GetInt32(0),
            FirstName = r.GetString(1),
            LastName  = r.GetString(2),
            Email     = r.GetString(3),
        };
    }
}
