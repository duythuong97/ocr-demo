import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface PayrollRun {
  runId: number;
  periodStart: string;
  periodEnd: string;
  status: string;
  totalGross: number;
  totalNet: number;
}

@Injectable({ providedIn: 'root' })
export class PayrollService {
  private readonly base = '/api/v1';

  constructor(private http: HttpClient, private employeeService: EmployeeService) {}

  getPayrollRuns(): Observable<PayrollRun[]> {
    return this.http.get<PayrollRun[]>(`${this.base}/payroll/runs`);
  }

  runPayroll(periodStart: string, periodEnd: string): Observable<PayrollRun> {
    return this.http.post<PayrollRun>(`${this.base}/payroll/runs`, { periodStart, periodEnd });
  }

  getPayslip(empId: number, runId: number): Observable<any> {
    return this.http.get<any>(`${this.base}/payroll/runs/${runId}/payslips/${empId}`);
  }
}

// Avoid circular import — forward declaration used above
import { EmployeeService } from './employee.service';
