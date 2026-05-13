CREATE OR REPLACE PACKAGE BODY HR.PKG_EMPLOYEE AS
    -- ============================================================
    -- Package Body: PKG_EMPLOYEE
    -- ============================================================

    -- ── Private helpers ──────────────────────────────────────────

    PROCEDURE log_audit(
        p_table_name IN VARCHAR2,
        p_operation  IN VARCHAR2,
        p_record_id  IN NUMBER,
        p_old_vals   IN CLOB DEFAULT NULL,
        p_new_vals   IN CLOB DEFAULT NULL
    ) IS
    BEGIN
        INSERT INTO HR.AUDIT_LOG (LOG_ID, TABLE_NAME, OPERATION, RECORD_ID, OLD_VALUES, NEW_VALUES, CHANGED_BY)
        VALUES (
            HR.SEQ_AUDIT.NEXTVAL,
            p_table_name, p_operation, p_record_id,
            p_old_vals, p_new_vals,
            SYS_CONTEXT('USERENV','SESSION_USER')
        );
    END log_audit;

    -- ── Public procedures ─────────────────────────────────────────

    PROCEDURE get_employee(
        p_emp_id    IN  HR.EMPLOYEES.EMP_ID%TYPE,
        p_emp_rec   OUT HR.EMPLOYEES%ROWTYPE,
        p_result    OUT NUMBER,
        p_message   OUT VARCHAR2
    ) IS
    BEGIN
        SELECT * INTO p_emp_rec
        FROM HR.EMPLOYEES
        WHERE EMP_ID = p_emp_id;

        p_result  := 0;
        p_message := 'OK';
    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            p_result  := -1;
            p_message := 'Employee not found: ' || p_emp_id;
        WHEN OTHERS THEN
            p_result  := -99;
            p_message := SQLERRM;
    END get_employee;

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
    ) IS
        v_emp_id  HR.EMPLOYEES.EMP_ID%TYPE;
        v_job_rec HR.JOBS%ROWTYPE;
    BEGIN
        -- Validate job exists and salary within range
        SELECT * INTO v_job_rec
        FROM HR.JOBS
        WHERE JOB_ID = p_job_id;

        IF p_salary < v_job_rec.MIN_SALARY OR p_salary > v_job_rec.MAX_SALARY THEN
            p_result  := -2;
            p_message := 'Salary ' || p_salary || ' is outside range for job ' || p_job_id;
            RETURN;
        END IF;

        -- Check department exists
        DECLARE
            v_dept_count NUMBER;
        BEGIN
            SELECT COUNT(1) INTO v_dept_count
            FROM HR.DEPARTMENTS
            WHERE DEPT_ID = p_dept_id;

            IF v_dept_count = 0 THEN
                p_result  := -3;
                p_message := 'Department not found: ' || p_dept_id;
                RETURN;
            END IF;
        END;

        v_emp_id := HR.SEQ_EMPLOYEES.NEXTVAL;

        INSERT INTO HR.EMPLOYEES (
            EMP_ID, FIRST_NAME, LAST_NAME, EMAIL,
            JOB_ID, SALARY, DEPT_ID, HIRE_DATE, STATUS
        ) VALUES (
            v_emp_id, p_first_name, p_last_name, p_email,
            p_job_id, p_salary, p_dept_id, p_hire_date, 'ACTIVE'
        );

        -- Seed initial salary history
        INSERT INTO HR.SALARY_HISTORY (HIST_ID, EMP_ID, OLD_SALARY, NEW_SALARY, CHANGE_DATE, REASON)
        VALUES (HR.SEQ_SALARY_HIST.NEXTVAL, v_emp_id, NULL, p_salary, p_hire_date, 'Initial hire');

        log_audit('EMPLOYEES', 'INSERT', v_emp_id, NULL,
                  '{"salary":' || p_salary || ',"job":"' || p_job_id || '"}');

        p_new_emp_id := v_emp_id;
        p_result     := 0;
        p_message    := 'Employee created: ' || v_emp_id;

        COMMIT;
    EXCEPTION
        WHEN DUP_VAL_ON_INDEX THEN
            ROLLBACK;
            p_result  := -4;
            p_message := 'Duplicate email: ' || p_email;
        WHEN OTHERS THEN
            ROLLBACK;
            p_result  := -99;
            p_message := SQLERRM;
    END create_employee;

    PROCEDURE update_salary(
        p_emp_id      IN  HR.EMPLOYEES.EMP_ID%TYPE,
        p_new_salary  IN  HR.EMPLOYEES.SALARY%TYPE,
        p_reason      IN  VARCHAR2 DEFAULT NULL,
        p_result      OUT NUMBER,
        p_message     OUT VARCHAR2
    ) IS
        v_old_salary  HR.EMPLOYEES.SALARY%TYPE;
        v_job_id      HR.EMPLOYEES.JOB_ID%TYPE;
    BEGIN
        SELECT SALARY, JOB_ID
        INTO v_old_salary, v_job_id
        FROM HR.EMPLOYEES
        WHERE EMP_ID = p_emp_id AND STATUS = 'ACTIVE'
        FOR UPDATE;

        IF NOT is_salary_valid(v_job_id, p_new_salary) THEN
            p_result  := -2;
            p_message := 'New salary is outside job grade range';
            RETURN;
        END IF;

        UPDATE HR.EMPLOYEES
        SET SALARY     = p_new_salary,
            UPDATED_AT = SYSDATE
        WHERE EMP_ID = p_emp_id;

        INSERT INTO HR.SALARY_HISTORY (HIST_ID, EMP_ID, OLD_SALARY, NEW_SALARY, CHANGE_DATE, CHANGED_BY, REASON)
        VALUES (
            HR.SEQ_SALARY_HIST.NEXTVAL,
            p_emp_id, v_old_salary, p_new_salary, SYSDATE,
            SYS_CONTEXT('USERENV','SESSION_USER'), p_reason
        );

        log_audit('EMPLOYEES', 'UPDATE', p_emp_id,
                  '{"salary":' || v_old_salary || '}',
                  '{"salary":' || p_new_salary || '}');

        p_result  := 0;
        p_message := 'Salary updated';
        COMMIT;
    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            p_result  := -1;
            p_message := 'Active employee not found: ' || p_emp_id;
        WHEN OTHERS THEN
            ROLLBACK;
            p_result  := -99;
            p_message := SQLERRM;
    END update_salary;

    PROCEDURE terminate_employee(
        p_emp_id  IN  HR.EMPLOYEES.EMP_ID%TYPE,
        p_reason  IN  VARCHAR2,
        p_result  OUT NUMBER,
        p_message OUT VARCHAR2
    ) IS
        v_leave_count NUMBER;
    BEGIN
        -- Cancel pending leave requests
        SELECT COUNT(1) INTO v_leave_count
        FROM HR.LEAVE_REQUESTS
        WHERE EMP_ID = p_emp_id AND STATUS = 'PENDING';

        IF v_leave_count > 0 THEN
            UPDATE HR.LEAVE_REQUESTS
            SET STATUS = 'CANCELLED'
            WHERE EMP_ID = p_emp_id AND STATUS = 'PENDING';
        END IF;

        UPDATE HR.EMPLOYEES
        SET STATUS     = 'TERMINATED',
            UPDATED_AT = SYSDATE
        WHERE EMP_ID = p_emp_id AND STATUS = 'ACTIVE';

        IF SQL%ROWCOUNT = 0 THEN
            p_result  := -1;
            p_message := 'Active employee not found: ' || p_emp_id;
            RETURN;
        END IF;

        log_audit('EMPLOYEES', 'TERMINATE', p_emp_id,
                  '{"status":"ACTIVE"}', '{"status":"TERMINATED","reason":"' || p_reason || '"}');

        p_result  := 0;
        p_message := 'Employee terminated';
        COMMIT;
    EXCEPTION
        WHEN OTHERS THEN
            ROLLBACK;
            p_result  := -99;
            p_message := SQLERRM;
    END terminate_employee;

    -- ── Functions ─────────────────────────────────────────────────

    FUNCTION get_dept_headcount(
        p_dept_id IN HR.DEPARTMENTS.DEPT_ID%TYPE
    ) RETURN NUMBER IS
        v_count NUMBER;
    BEGIN
        SELECT COUNT(1) INTO v_count
        FROM HR.EMPLOYEES
        WHERE DEPT_ID = p_dept_id AND STATUS = 'ACTIVE';
        RETURN v_count;
    END get_dept_headcount;

    FUNCTION is_salary_valid(
        p_job_id IN HR.JOBS.JOB_ID%TYPE,
        p_salary IN NUMBER
    ) RETURN BOOLEAN IS
        v_min NUMBER;
        v_max NUMBER;
    BEGIN
        SELECT MIN_SALARY, MAX_SALARY
        INTO v_min, v_max
        FROM HR.JOBS
        WHERE JOB_ID = p_job_id;

        RETURN (p_salary BETWEEN v_min AND v_max);
    EXCEPTION
        WHEN NO_DATA_FOUND THEN RETURN FALSE;
    END is_salary_valid;

END PKG_EMPLOYEE;
/
