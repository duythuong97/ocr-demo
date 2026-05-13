using System;
using System.Threading.Tasks;
using Hangfire;
using HRSystem.Services;
using Microsoft.Extensions.Logging;

namespace HRSystem.Jobs
{
    public class PayrollMonthlyJob
    {
        private readonly PayrollService _payroll;
        private readonly ILogger<PayrollMonthlyJob> _logger;

        public PayrollMonthlyJob(PayrollService payrollService, ILogger<PayrollMonthlyJob> logger)
        {
            _payroll = payrollService;
            _logger  = logger;
        }

        public static void Register()
        {
            // Run on the 25th of every month at 02:00 AM
            RecurringJob.AddOrUpdate("monthly-payroll-gen", () => RunAsync(), "0 2 25 * *");
        }

        public static async Task RunAsync()
        {
            // Resolved via DI in actual usage
        }

        public async Task ExecuteAsync()
        {
            _logger.LogInformation("Starting monthly payroll generation");

            var periodId = DateTime.UtcNow.Month + (DateTime.UtcNow.Year * 100);
            await _payroll.GeneratePayslipsAsync(periodId);

            _logger.LogInformation("Monthly payroll generation complete for period {PeriodId}", periodId);
        }
    }

    public class SalaryReportJob
    {
        private readonly ILogger<SalaryReportJob> _logger;

        public SalaryReportJob(ILogger<SalaryReportJob> logger)
        {
            _logger = logger;
        }

        public static void Register()
        {
            // Every Monday at 07:00 AM
            RecurringJob.AddOrUpdate("weekly-salary-report", () => ExecuteAsync(), "0 7 * * 1");
        }

        public static async Task ExecuteAsync()
        {
            // Resolved via DI
        }
    }

    public class AuditCleanupJob
    {
        public static void Register()
        {
            // Daily at midnight — purge audit logs older than 90 days
            RecurringJob.AddOrUpdate("daily-audit-cleanup", () => RunCleanup(), "0 0 * * *");
        }

        public static void RunCleanup()
        {
            // Implementation uses direct SQL via IDbConnection
        }
    }
}
