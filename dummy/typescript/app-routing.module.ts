import { NgModule } from '@angular/core';
import { RouterModule, Routes } from '@angular/router';
import { EmployeeListComponent } from './pages/employee-list.component';
import { PayrollDashboardComponent } from './pages/payroll-dashboard.component';

const routes: Routes = [
  { path: 'employees', component: EmployeeListComponent },
  { path: 'payroll', component: PayrollDashboardComponent },
  { path: '', redirectTo: 'employees', pathMatch: 'full' },
];

@NgModule({
  imports: [RouterModule.forRoot(routes)],
  exports: [RouterModule],
  declarations: [EmployeeListComponent, PayrollDashboardComponent],
  providers: [EmployeeService, PayrollService],
})
export class AppRoutingModule {}

import { EmployeeService } from './services/employee.service';
import { PayrollService } from './services/payroll.service';
