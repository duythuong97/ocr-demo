import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface Employee {
  empId: number;
  firstName: string;
  lastName: string;
  email: string;
  salary: number;
  deptName: string;
  jobTitle: string;
  status: string;
  hireDate: string;
}

@Injectable({ providedIn: 'root' })
export class EmployeeService {
  private readonly base = '/api/v1';

  constructor(private http: HttpClient) {}

  getEmployee(empId: number): Observable<Employee> {
    return this.http.get<Employee>(`${this.base}/employees/${empId}`);
  }

  searchEmployees(keyword: string, deptId?: number): Observable<Employee[]> {
    return this.http.get<Employee[]>(`${this.base}/employees`, {
      params: { q: keyword, ...(deptId ? { dept_id: deptId } : {}) },
    });
  }

  createEmployee(payload: Partial<Employee>): Observable<Employee> {
    return this.http.post<Employee>(`${this.base}/employees`, payload);
  }

  updateSalary(empId: number, newSalary: number, reason: string): Observable<void> {
    return this.http.patch<void>(`${this.base}/employees/${empId}/salary`, { newSalary, reason });
  }

  terminateEmployee(empId: number, reason: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/employees/${empId}`, { body: { reason } });
  }

  getDepartments(): Observable<any[]> {
    return this.http.get<any[]>(`${this.base}/departments`);
  }
}
