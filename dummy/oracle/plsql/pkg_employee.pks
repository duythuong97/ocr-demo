CREATE OR REPLACE PACKAGE HR.PKG_EMPLOYEE AS
    -- ============================================================
    -- Package: PKG_EMPLOYEE
    -- Description: Employee management operations
    -- ============================================================

    -- Get employee record by ID
    PROCEDURE get_employee(
        p_emp_id    IN  HR.EMPLOYEES.EMP_ID%TYPE,
        p_emp_rec   OUT HR.EMPLOYEES%ROWTYPE,
        p_result    OUT NUMBER,
        p_message   OUT VARCHAR2
    );

    -- Create a new employee
    PROCEDURE create_employee(
        p_first_name  IN  HR.EMPLOYEES.FIRST_NAME%TYPE,
        p_last_name   IN  HR.EMPLOYEES.LAST_NAME%TYPE,
        p_email       IN  HR.EMPLOYEES.EMAIL%TYPE,
        p_job_id      IN  HR.EMPLOYEES.JOB_ID%TYPE,
        p_salary      IN  HR.EMPLOYEES.SALARY%TYPE,
        p_dept_id     IN  HR.EMPLOYEES.DEPT_ID%TYPE,
        p_hire_date   IN  HR.EMPLOYEES.HIRE_DATE%TYPE DEFAULT SYSDATE,
        p_new_emp_id  OUT HR.EMPLOYEES.EMP_ID%TYPE,
        p_result      OUT NUMBER,
        p_message     OUT VARCHAR2
    );

    -- Update salary for employee
    PROCEDURE update_salary(
        p_emp_id      IN  HR.EMPLOYEES.EMP_ID%TYPE,
        p_new_salary  IN  HR.EMPLOYEES.SALARY%TYPE,
        p_reason      IN  VARCHAR2 DEFAULT NULL,
        p_result      OUT NUMBER,
        p_message     OUT VARCHAR2
    );

    -- Terminate employee
    PROCEDURE terminate_employee(
        p_emp_id      IN  HR.EMPLOYEES.EMP_ID%TYPE,
        p_reason      IN  VARCHAR2,
        p_result      OUT NUMBER,
        p_message     OUT VARCHAR2
    );

    -- Count active employees per department
    FUNCTION get_dept_headcount(
        p_dept_id IN HR.DEPARTMENTS.DEPT_ID%TYPE
    ) RETURN NUMBER;

    -- Check if salary is within job range
    FUNCTION is_salary_valid(
        p_job_id  IN HR.JOBS.JOB_ID%TYPE,
        p_salary  IN NUMBER
    ) RETURN BOOLEAN;

END PKG_EMPLOYEE;
/
