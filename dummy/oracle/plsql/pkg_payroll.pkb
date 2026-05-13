CREATE OR REPLACE PACKAGE BODY HR.PKG_PAYROLL AS
    -- ============================================================
    -- Package Body: PKG_PAYROLL
    -- ============================================================

    FUNCTION calc_tax(p_gross IN NUMBER) RETURN NUMBER IS
    BEGIN
        -- Simple progressive tax brackets
        IF p_gross <= 5000000 THEN
            RETURN p_gross * 0.05;
        ELSIF p_gross <= 10000000 THEN
            RETURN (5000000 * 0.05) + ((p_gross - 5000000) * 0.10);
        ELSIF p_gross <= 18000000 THEN
            RETURN (5000000 * 0.05) + (5000000 * 0.10) + ((p_gross - 10000000) * 0.15);
        ELSE
            RETURN (5000000 * 0.05) + (5000000 * 0.10) + (8000000 * 0.15) + ((p_gross - 18000000) * 0.20);
        END IF;
    END calc_tax;

    FUNCTION calc_insurance(p_gross IN NUMBER, p_rate IN NUMBER DEFAULT 0.08) RETURN NUMBER IS
    BEGIN
        RETURN ROUND(p_gross * p_rate, 2);
    END calc_insurance;

    PROCEDURE open_pay_period(
        p_period_name IN  PAYROLL.PAY_PERIODS.PERIOD_NAME%TYPE,
        p_start_date  IN  DATE,
        p_end_date    IN  DATE,
        p_pay_date    IN  DATE,
        p_period_id   OUT PAYROLL.PAY_PERIODS.PERIOD_ID%TYPE,
        p_result      OUT NUMBER,
        p_message     OUT VARCHAR2
    ) IS
        v_overlap NUMBER;
    BEGIN
        -- Check for overlapping periods
        SELECT COUNT(1) INTO v_overlap
        FROM PAYROLL.PAY_PERIODS
        WHERE STATUS != 'CLOSED'
          AND (p_start_date BETWEEN START_DATE AND END_DATE
               OR p_end_date BETWEEN START_DATE AND END_DATE);

        IF v_overlap > 0 THEN
            p_result  := -1;
            p_message := 'Overlapping pay period already exists';
            RETURN;
        END IF;

        p_period_id := PAYROLL.SEQ_PAY_PERIODS.NEXTVAL;

        INSERT INTO PAYROLL.PAY_PERIODS (PERIOD_ID, PERIOD_NAME, START_DATE, END_DATE, PAY_DATE, STATUS)
        VALUES (p_period_id, p_period_name, p_start_date, p_end_date, p_pay_date, 'OPEN');

        p_result  := 0;
        p_message := 'Pay period created: ' || p_period_id;
        COMMIT;
    EXCEPTION
        WHEN OTHERS THEN
            ROLLBACK;
            p_result  := -99;
            p_message := SQLERRM;
    END open_pay_period;

    PROCEDURE generate_payslips(
        p_period_id  IN  PAYROLL.PAY_PERIODS.PERIOD_ID%TYPE,
        p_result     OUT NUMBER,
        p_message    OUT VARCHAR2
    ) IS
        v_period     PAYROLL.PAY_PERIODS%ROWTYPE;
        v_count      NUMBER := 0;
        v_gross      NUMBER;
        v_tax        NUMBER;
        v_insurance  NUMBER;
        v_net        NUMBER;
        v_payslip_id NUMBER;

        CURSOR c_employees IS
            SELECT e.EMP_ID, e.SALARY,
                   NVL(SUM(a.AMOUNT), 0) AS TOTAL_ALLOWANCE
            FROM HR.EMPLOYEES e
            LEFT JOIN PAYROLL.ALLOWANCES a
                ON e.EMP_ID = a.EMP_ID
                AND a.EFFECTIVE_FROM <= v_period.END_DATE
                AND (a.EFFECTIVE_TO IS NULL OR a.EFFECTIVE_TO >= v_period.START_DATE)
            WHERE e.STATUS = 'ACTIVE'
            GROUP BY e.EMP_ID, e.SALARY;
    BEGIN
        SELECT * INTO v_period
        FROM PAYROLL.PAY_PERIODS
        WHERE PERIOD_ID = p_period_id AND STATUS = 'OPEN';

        -- Remove any existing draft payslips for re-generation
        DELETE FROM PAYROLL.PAYSLIPS
        WHERE PERIOD_ID = p_period_id AND STATUS = 'DRAFT';

        FOR emp IN c_employees LOOP
            v_gross     := emp.SALARY + emp.TOTAL_ALLOWANCE;
            v_tax       := calc_tax(v_gross);
            v_insurance := calc_insurance(v_gross);
            v_net       := v_gross - v_tax - v_insurance;

            v_payslip_id := PAYROLL.SEQ_PAYSLIPS.NEXTVAL;

            INSERT INTO PAYROLL.PAYSLIPS (
                PAYSLIP_ID, EMP_ID, PERIOD_ID,
                GROSS_PAY, TAX_AMOUNT, INSURANCE, NET_PAY, STATUS
            ) VALUES (
                v_payslip_id, emp.EMP_ID, p_period_id,
                v_gross, v_tax, v_insurance, v_net, 'DRAFT'
            );

            v_count := v_count + 1;
        END LOOP;

        p_result  := 0;
        p_message := v_count || ' payslips generated';
        COMMIT;
    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            p_result  := -1;
            p_message := 'Open pay period not found: ' || p_period_id;
        WHEN OTHERS THEN
            ROLLBACK;
            p_result  := -99;
            p_message := SQLERRM;
    END generate_payslips;

    PROCEDURE approve_payslip(
        p_payslip_id IN  PAYROLL.PAYSLIPS.PAYSLIP_ID%TYPE,
        p_result     OUT NUMBER,
        p_message    OUT VARCHAR2
    ) IS
    BEGIN
        UPDATE PAYROLL.PAYSLIPS
        SET STATUS       = 'APPROVED',
            PROCESSED_BY = SYS_CONTEXT('USERENV','SESSION_USER')
        WHERE PAYSLIP_ID = p_payslip_id AND STATUS = 'DRAFT';

        IF SQL%ROWCOUNT = 0 THEN
            p_result  := -1;
            p_message := 'Draft payslip not found: ' || p_payslip_id;
            RETURN;
        END IF;

        p_result  := 0;
        p_message := 'Payslip approved';
        COMMIT;
    EXCEPTION
        WHEN OTHERS THEN
            ROLLBACK;
            p_result  := -99;
            p_message := SQLERRM;
    END approve_payslip;

    PROCEDURE close_pay_period(
        p_period_id  IN  PAYROLL.PAY_PERIODS.PERIOD_ID%TYPE,
        p_result     OUT NUMBER,
        p_message    OUT VARCHAR2
    ) IS
        v_draft_count NUMBER;
    BEGIN
        SELECT COUNT(1) INTO v_draft_count
        FROM PAYROLL.PAYSLIPS
        WHERE PERIOD_ID = p_period_id AND STATUS = 'DRAFT';

        IF v_draft_count > 0 THEN
            p_result  := -1;
            p_message := v_draft_count || ' payslips still in DRAFT status';
            RETURN;
        END IF;

        UPDATE PAYROLL.PAY_PERIODS
        SET STATUS = 'CLOSED'
        WHERE PERIOD_ID = p_period_id AND STATUS IN ('OPEN','PROCESSING');

        p_result  := 0;
        p_message := 'Pay period closed';
        COMMIT;
    EXCEPTION
        WHEN OTHERS THEN
            ROLLBACK;
            p_result  := -99;
            p_message := SQLERRM;
    END close_pay_period;

END PKG_PAYROLL;
/
