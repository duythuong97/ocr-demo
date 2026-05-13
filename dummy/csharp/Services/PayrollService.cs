using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using Microsoft.Data.SqlClient;
using HRSystem.Models;

namespace HRSystem.Services
{
    public class PayrollService
    {
        private readonly string _connStr;

        public PayrollService(string connectionString)
        {
            _connStr = connectionString;
        }

        public async Task<List<Payslip>> GetPayslipsByPeriodAsync(int periodId)
        {
            const string sql = @"
                SELECT p.PAYSLIP_ID, p.EMP_ID,
                       e.FIRST_NAME + ' ' + e.LAST_NAME AS EMP_NAME,
                       p.GROSS_PAY, p.TAX_AMOUNT, p.INSURANCE, p.NET_PAY, p.STATUS
                FROM PAYSLIPS p
                JOIN EMPLOYEES e ON p.EMP_ID = e.EMP_ID
                WHERE p.PERIOD_ID = @periodId
                ORDER BY e.LAST_NAME, e.FIRST_NAME";

            using var conn = new SqlConnection(_connStr);
            using var cmd  = new SqlCommand(sql, conn);
            cmd.Parameters.AddWithValue("@periodId", periodId);
            await conn.OpenAsync();

            var list   = new List<Payslip>();
            using var reader = await cmd.ExecuteReaderAsync();
            while (await reader.ReadAsync())
                list.Add(new Payslip { PayslipId = reader.GetInt32(0) });

            return list;
        }

        public async Task GeneratePayslipsAsync(int periodId)
        {
            const string deleteSql = @"
                DELETE FROM PAYSLIPS
                WHERE PERIOD_ID = @periodId AND STATUS = 'DRAFT'";

            const string insertSql = @"
                INSERT INTO PAYSLIPS (EMP_ID, PERIOD_ID, GROSS_PAY, TAX_AMOUNT, INSURANCE, NET_PAY, STATUS)
                SELECT
                    e.EMP_ID,
                    @periodId,
                    e.SALARY + ISNULL(a.TOTAL_ALLOWANCE, 0),
                    dbo.fn_CalcTax(e.SALARY + ISNULL(a.TOTAL_ALLOWANCE, 0)),
                    (e.SALARY + ISNULL(a.TOTAL_ALLOWANCE, 0)) * 0.08,
                    (e.SALARY + ISNULL(a.TOTAL_ALLOWANCE, 0))
                        - dbo.fn_CalcTax(e.SALARY + ISNULL(a.TOTAL_ALLOWANCE, 0))
                        - (e.SALARY + ISNULL(a.TOTAL_ALLOWANCE, 0)) * 0.08,
                    'DRAFT'
                FROM EMPLOYEES e
                LEFT JOIN (
                    SELECT EMP_ID, SUM(AMOUNT) AS TOTAL_ALLOWANCE
                    FROM ALLOWANCES
                    WHERE EFFECTIVE_FROM <= GETDATE() AND (EFFECTIVE_TO IS NULL OR EFFECTIVE_TO >= GETDATE())
                    GROUP BY EMP_ID
                ) a ON e.EMP_ID = a.EMP_ID
                WHERE e.STATUS = 'ACTIVE'";

            using var conn = new SqlConnection(_connStr);
            await conn.OpenAsync();
            using var tx = conn.BeginTransaction();

            var cmd = new SqlCommand(deleteSql, conn, tx);
            cmd.Parameters.AddWithValue("@periodId", periodId);
            await cmd.ExecuteNonQueryAsync();

            cmd = new SqlCommand(insertSql, conn, tx);
            cmd.Parameters.AddWithValue("@periodId", periodId);
            await cmd.ExecuteNonQueryAsync();

            tx.Commit();
        }

        public async Task<int> ApproveAllPayslipsAsync(int periodId)
        {
            const string sql = @"
                UPDATE PAYSLIPS
                SET STATUS = 'APPROVED', PROCESSED_BY = SYSTEM_USER
                WHERE PERIOD_ID = @periodId AND STATUS = 'DRAFT'";

            using var conn = new SqlConnection(_connStr);
            using var cmd  = new SqlCommand(sql, conn);
            cmd.Parameters.AddWithValue("@periodId", periodId);
            await conn.OpenAsync();
            return await cmd.ExecuteNonQueryAsync();
        }

        public async Task<PayPeriodSummary> GetPeriodSummaryAsync(int periodId)
        {
            const string sql = @"
                SELECT
                    pp.PERIOD_NAME,
                    pp.START_DATE,
                    pp.END_DATE,
                    COUNT(ps.PAYSLIP_ID)   AS PAYSLIP_COUNT,
                    SUM(ps.GROSS_PAY)      AS TOTAL_GROSS,
                    SUM(ps.NET_PAY)        AS TOTAL_NET,
                    SUM(ps.TAX_AMOUNT)     AS TOTAL_TAX
                FROM PAY_PERIODS pp
                LEFT JOIN PAYSLIPS ps ON pp.PERIOD_ID = ps.PERIOD_ID
                WHERE pp.PERIOD_ID = @periodId
                GROUP BY pp.PERIOD_NAME, pp.START_DATE, pp.END_DATE";

            using var conn = new SqlConnection(_connStr);
            using var cmd  = new SqlCommand(sql, conn);
            cmd.Parameters.AddWithValue("@periodId", periodId);
            await conn.OpenAsync();

            using var reader = await cmd.ExecuteReaderAsync();
            if (!await reader.ReadAsync()) return null;
            return new PayPeriodSummary { PeriodName = reader.GetString(0) };
        }
    }
}
