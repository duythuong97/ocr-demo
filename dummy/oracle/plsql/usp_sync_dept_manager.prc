CREATE OR REPLACE PROCEDURE HR.USP_SYNC_DEPT_MANAGER
(
    p_dept_id    IN  HR.DEPARTMENTS.DEPT_ID%TYPE,
    p_manager_id IN  HR.EMPLOYEES.EMP_ID%TYPE,
    p_result     OUT NUMBER,
    p_message    OUT VARCHAR2
) AS
-- ============================================================
-- Procedure: USP_SYNC_DEPT_MANAGER
-- Description: Assign or change a department manager.
--   Validates the manager is an active employee in the department,
--   updates DEPARTMENTS, and writes an audit record.
-- ============================================================
    v_emp_count   NUMBER;
    v_old_manager HR.DEPARTMENTS.MANAGER_ID%TYPE;
BEGIN
    -- Validate manager is active
    SELECT COUNT(1) INTO v_emp_count
    FROM HR.EMPLOYEES
    WHERE EMP_ID = p_manager_id AND STATUS = 'ACTIVE';

    IF v_emp_count = 0 THEN
        p_result  := -1;
        p_message := 'Manager must be an active employee';
        RETURN;
    END IF;

    -- Get existing manager
    SELECT MANAGER_ID INTO v_old_manager
    FROM HR.DEPARTMENTS
    WHERE DEPT_ID = p_dept_id;

    -- Move manager into the department if not already assigned
    UPDATE HR.EMPLOYEES
    SET DEPT_ID    = p_dept_id,
        UPDATED_AT = SYSDATE
    WHERE EMP_ID = p_manager_id
      AND (DEPT_ID != p_dept_id OR DEPT_ID IS NULL);

    -- Update department manager
    UPDATE HR.DEPARTMENTS
    SET MANAGER_ID = p_manager_id
    WHERE DEPT_ID  = p_dept_id;

    -- Audit
    INSERT INTO HR.AUDIT_LOG (LOG_ID, TABLE_NAME, OPERATION, RECORD_ID, OLD_VALUES, NEW_VALUES, CHANGED_BY)
    VALUES (
        HR.SEQ_AUDIT.NEXTVAL,
        'DEPARTMENTS', 'UPDATE', p_dept_id,
        '{"manager_id":' || NVL(TO_CHAR(v_old_manager), 'null') || '}',
        '{"manager_id":' || p_manager_id || '}',
        SYS_CONTEXT('USERENV','SESSION_USER')
    );

    p_result  := 0;
    p_message := 'Department manager updated';
    COMMIT;
EXCEPTION
    WHEN NO_DATA_FOUND THEN
        p_result  := -2;
        p_message := 'Department not found: ' || p_dept_id;
    WHEN OTHERS THEN
        ROLLBACK;
        p_result  := -99;
        p_message := SQLERRM;
END USP_SYNC_DEPT_MANAGER;
/
