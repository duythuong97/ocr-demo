CREATE OR REPLACE FUNCTION HR.FNC_GET_DEPT_BUDGET
(
    p_dept_id IN HR.DEPARTMENTS.DEPT_ID%TYPE
) RETURN NUMBER AS
-- ============================================================
-- Function: FNC_GET_DEPT_BUDGET
-- Returns: Total salary + allowances budget for a department
-- ============================================================
    v_salary_total    NUMBER := 0;
    v_allowance_total NUMBER := 0;
BEGIN
    -- Sum active employee salaries
    SELECT NVL(SUM(e.SALARY), 0)
    INTO v_salary_total
    FROM HR.EMPLOYEES e
    WHERE e.DEPT_ID = p_dept_id AND e.STATUS = 'ACTIVE';

    -- Sum active allowances for those employees
    SELECT NVL(SUM(a.AMOUNT), 0)
    INTO v_allowance_total
    FROM PAYROLL.ALLOWANCES a
    JOIN HR.EMPLOYEES e ON a.EMP_ID = e.EMP_ID
    WHERE e.DEPT_ID = p_dept_id
      AND e.STATUS  = 'ACTIVE'
      AND a.EFFECTIVE_FROM <= SYSDATE
      AND (a.EFFECTIVE_TO IS NULL OR a.EFFECTIVE_TO >= SYSDATE);

    RETURN v_salary_total + v_allowance_total;
EXCEPTION
    WHEN OTHERS THEN RETURN 0;
END FNC_GET_DEPT_BUDGET;
/
