import axios from 'axios';

const BASE_URL = '/api/v1';

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

export interface EmployeeCreateRequest {
  firstName: string;
  lastName: string;
  email: string;
  jobId: string;
  salary: number;
  deptId: number;
  hireDate?: string;
}

// ── Employee CRUD ──────────────────────────────────────────────────────────

export async function fetchEmployee(empId: number): Promise<Employee> {
  const res = await axios.get(`${BASE_URL}/employees/${empId}`);
  return res.data;
}

export async function searchEmployees(keyword: string, deptId?: number): Promise<Employee[]> {
  const res = await axios.get(`${BASE_URL}/employees`, {
    params: { q: keyword, dept_id: deptId },
  });
  return res.data.items;
}

export async function createEmployee(payload: EmployeeCreateRequest): Promise<Employee> {
  const res = await axios.post(`${BASE_URL}/employees`, payload);
  return res.data;
}

export async function updateSalary(empId: number, newSalary: number, reason: string): Promise<void> {
  await axios.patch(`${BASE_URL}/employees/${empId}/salary`, { newSalary, reason });
}

export async function terminateEmployee(empId: number, reason: string): Promise<void> {
  await axios.delete(`${BASE_URL}/employees/${empId}`, { data: { reason } });
}

// ── Departments ────────────────────────────────────────────────────────────

export async function fetchDepartments() {
  const res = await axios.get(`${BASE_URL}/departments`);
  return res.data;
}

export async function fetchDeptHeadcount(deptId: number) {
  const res = await axios.get(`${BASE_URL}/departments/${deptId}/headcount`);
  return res.data;
}

// ── Leave requests ─────────────────────────────────────────────────────────

export async function submitLeaveRequest(empId: number, payload: object) {
  const res = await axios.post(`${BASE_URL}/employees/${empId}/leave`, payload);
  return res.data;
}

export async function approveLeave(reqId: number): Promise<void> {
  await axios.patch(`${BASE_URL}/leave/${reqId}/approve`);
}

export async function rejectLeave(reqId: number, notes: string): Promise<void> {
  await axios.patch(`${BASE_URL}/leave/${reqId}/reject`, { notes });
}
