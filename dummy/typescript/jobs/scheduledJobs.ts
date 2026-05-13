import { Injectable, Logger } from '@nestjs/common';
import { Cron, CronExpression } from '@nestjs/schedule';
import axios from 'axios';
import { PayrollService } from '../services/payroll.service';

const NOTIFY_WEBHOOK = 'https://hooks.slack.com/services/T000/B000/xxxx';

@Injectable()
export class ScheduledJobs {
  private readonly logger = new Logger(ScheduledJobs.name);

  constructor(private readonly payrollService: PayrollService) {}

  // ── Every 1st of month at 06:00 – auto-open new pay period ──────────────
  @Cron('0 6 1 * *')
  async openMonthlyPayPeriod() {
    this.logger.log('Opening monthly pay period');
    const now   = new Date();
    const year  = now.getFullYear();
    const month = now.getMonth() + 1;

    const startDate = new Date(year, month - 1, 1);
    const endDate   = new Date(year, month, 0); // last day of month
    const payDate   = new Date(year, month - 1, 25);

    await axios.post('/api/v1/payroll/periods', {
      periodName: `${year}-${String(month).padStart(2, '0')}`,
      startDate: startDate.toISOString(),
      endDate:   endDate.toISOString(),
      payDate:   payDate.toISOString(),
    });
  }

  // ── 24th of every month at 08:00 – generate payslips ────────────────────
  @Cron('0 8 24 * *')
  async generateMonthlyPayslips() {
    this.logger.log('Generating monthly payslips');
    const periodId = await this.payrollService.getCurrentOpenPeriodId();
    if (!periodId) {
      this.logger.warn('No open pay period found');
      return;
    }
    const result = await axios.post(`/api/v1/payroll/periods/${periodId}/generate`);
    this.logger.log(`Generated ${result.data.count} payslips`);
  }

  // ── Every Monday 09:00 – send salary summary to Slack ───────────────────
  @Cron('0 9 * * 1')
  async weeklySalarySummary() {
    this.logger.log('Sending weekly salary summary');
    const summary = await axios.get('/api/v1/payroll/summary/weekly');
    await axios.post(NOTIFY_WEBHOOK, {
      text: `Weekly payroll summary:\n${JSON.stringify(summary.data, null, 2)}`,
    });
  }

  // ── Daily at 00:30 – cleanup old audit records ───────────────────────────
  @Cron(CronExpression.EVERY_DAY_AT_MIDNIGHT)
  async auditLogCleanup() {
    this.logger.log('Running audit log cleanup');
    await axios.delete('/api/v1/admin/audit/cleanup', {
      params: { olderThanDays: 90 },
    });
  }
}
