import axios from 'axios';

const PAYROLL_API = '/api/v1/payroll';
const REPORT_API  = 'https://reports.internal.company.com/api';

export interface PayPeriod {
  periodId: number;
  periodName: string;
  startDate: string;
  endDate: string;
  payDate: string;
  status: 'OPEN' | 'PROCESSING' | 'CLOSED';
}

export interface Payslip {
  payslipId: number;
  empId: number;
  empName: string;
  grossPay: number;
  taxAmount: number;
  insurance: number;
  netPay: number;
  status: string;
}

// ── Pay Periods ────────────────────────────────────────────────────────────

export async function getPayPeriods(): Promise<PayPeriod[]> {
  const res = await axios.get(`${PAYROLL_API}/periods`);
  return res.data;
}

export async function openPayPeriod(payload: Omit<PayPeriod, 'periodId' | 'status'>): Promise<PayPeriod> {
  const res = await axios.post(`${PAYROLL_API}/periods`, payload);
  return res.data;
}

export async function closePayPeriod(periodId: number): Promise<void> {
  await axios.patch(`${PAYROLL_API}/periods/${periodId}/close`);
}

// ── Payslips ───────────────────────────────────────────────────────────────

export async function generatePayslips(periodId: number): Promise<{ count: number }> {
  const res = await axios.post(`${PAYROLL_API}/periods/${periodId}/generate`);
  return res.data;
}

export async function getPayslips(periodId: number): Promise<Payslip[]> {
  const res = await axios.get(`${PAYROLL_API}/periods/${periodId}/payslips`);
  return res.data;
}

export async function approvePayslip(payslipId: number): Promise<void> {
  await axios.patch(`${PAYROLL_API}/payslips/${payslipId}/approve`);
}

export async function approveAllPayslips(periodId: number): Promise<void> {
  await axios.post(`${PAYROLL_API}/periods/${periodId}/approve-all`);
}

// ── External: Report generation ────────────────────────────────────────────

export async function triggerPayrollReport(periodId: number): Promise<string> {
  const res = await axios.post(`${REPORT_API}/payroll/generate`, { periodId });
  return res.data.reportUrl;
}

export async function downloadPayrollExport(periodId: number): Promise<Blob> {
  const res = await axios.get(`${PAYROLL_API}/periods/${periodId}/export`, {
    responseType: 'blob',
  });
  return res.data;
}
