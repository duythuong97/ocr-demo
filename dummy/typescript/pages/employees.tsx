import React, { useEffect, useState } from 'react';
import { fetchEmployee, searchEmployees, terminateEmployee, Employee } from '../services/employeeApi';

export default function EmployeesPage() {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [query, setQuery]         = useState('');
  const [loading, setLoading]     = useState(false);

  useEffect(() => {
    loadEmployees();
  }, []);

  async function loadEmployees() {
    setLoading(true);
    try {
      const data = await searchEmployees('');
      setEmployees(data);
    } finally {
      setLoading(false);
    }
  }

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    const data = await searchEmployees(query);
    setEmployees(data);
    setLoading(false);
  }

  async function handleTerminate(empId: number) {
    if (!confirm('Are you sure you want to terminate this employee?')) return;
    await terminateEmployee(empId, 'Voluntary resignation');
    loadEmployees();
  }

  return (
    <div className="employees-page">
      <h1>Employees</h1>
      <form onSubmit={handleSearch}>
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search..." />
        <button type="submit">Search</button>
      </form>
      {loading ? (
        <p>Loading...</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Name</th><th>Email</th><th>Department</th><th>Salary</th><th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {employees.map(emp => (
              <tr key={emp.empId}>
                <td>{emp.firstName} {emp.lastName}</td>
                <td>{emp.email}</td>
                <td>{emp.deptName}</td>
                <td>{emp.salary.toLocaleString()}</td>
                <td>
                  <button onClick={() => handleTerminate(emp.empId)}>Terminate</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
