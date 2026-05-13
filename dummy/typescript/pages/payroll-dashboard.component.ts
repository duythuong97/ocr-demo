import { Component, OnInit } from '@angular/core';
import { PayrollService, PayrollRun } from '../services/payroll.service';

@Component({
  selector: 'app-payroll-dashboard',
  templateUrl: './payroll-dashboard.component.html',
})
export class PayrollDashboardComponent implements OnInit {
  runs: PayrollRun[] = [];

  constructor(private payrollService: PayrollService) {}

  ngOnInit(): void {
    this.payrollService.getPayrollRuns().subscribe(data => { this.runs = data; });
  }

  triggerRun(start: string, end: string): void {
    this.payrollService.runPayroll(start, end).subscribe(() => this.ngOnInit());
  }
}
