CREATE OR REPLACE TRIGGER HR.TRG_EMPLOYEES_AUDIT
AFTER INSERT OR UPDATE OR DELETE ON HR.EMPLOYEES
FOR EACH ROW
-- ============================================================
-- Trigger: TRG_EMPLOYEES_AUDIT
-- Description: Auto-audit all changes to the EMPLOYEES table.
-- ============================================================
DECLARE
    v_operation VARCHAR2(10);
    v_record_id NUMBER;
    v_old_vals  CLOB := NULL;
    v_new_vals  CLOB := NULL;
BEGIN
    IF INSERTING THEN
        v_operation := 'INSERT';
        v_record_id := :NEW.EMP_ID;
        v_new_vals  := '{"first_name":"' || :NEW.FIRST_NAME ||
                       '","last_name":"'  || :NEW.LAST_NAME  ||
                       '","salary":'      || NVL(TO_CHAR(:NEW.SALARY), 'null') ||
                       ',"status":"'      || :NEW.STATUS || '"}';
    ELSIF UPDATING THEN
        v_operation := 'UPDATE';
        v_record_id := :NEW.EMP_ID;
        v_old_vals  := '{"salary":'  || NVL(TO_CHAR(:OLD.SALARY), 'null') ||
                       ',"status":"' || :OLD.STATUS ||
                       '","dept_id":' || NVL(TO_CHAR(:OLD.DEPT_ID), 'null') || '}';
        v_new_vals  := '{"salary":'  || NVL(TO_CHAR(:NEW.SALARY), 'null') ||
                       ',"status":"' || :NEW.STATUS ||
                       '","dept_id":' || NVL(TO_CHAR(:NEW.DEPT_ID), 'null') || '}';
    ELSE
        v_operation := 'DELETE';
        v_record_id := :OLD.EMP_ID;
        v_old_vals  := '{"email":"' || :OLD.EMAIL || '","status":"' || :OLD.STATUS || '"}';
    END IF;

    INSERT INTO HR.AUDIT_LOG (LOG_ID, TABLE_NAME, OPERATION, RECORD_ID, OLD_VALUES, NEW_VALUES, CHANGED_BY)
    VALUES (
        HR.SEQ_AUDIT.NEXTVAL,
        'EMPLOYEES', v_operation, v_record_id,
        v_old_vals, v_new_vals,
        SYS_CONTEXT('USERENV','SESSION_USER')
    );
END TRG_EMPLOYEES_AUDIT;
/
