import { Component, OnInit } from '@angular/core';
import { Router } from '@angular/router';
import { EmployeeService, Employee } from '../services/employee.service';

@Component({
  selector: 'app-employee-list',
  templateUrl: './employee-list.component.html',
})
export class EmployeeListComponent implements OnInit {
  employees: Employee[] = [];
  loading = false;

  constructor(
    private employeeService: EmployeeService,
    private router: Router,
  ) {}

  ngOnInit(): void {
    this.loadEmployees();
  }

  loadEmployees(): void {
    this.loading = true;
    this.employeeService.searchEmployees('').subscribe({
      next: (data) => { this.employees = data; this.loading = false; },
      error: () => { this.loading = false; },
    });
  }

  viewDetail(empId: number): void {
    this.router.navigate(['/employees', empId]);
  }
}
