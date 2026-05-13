CREATE OR REPLACE PACKAGE HR.PKG_PAYROLL AS
    -- ============================================================
    -- Package: PKG_PAYROLL
    -- Description: Payroll processing operations
    -- ============================================================

    PROCEDURE open_pay_period(
        p_period_name IN  PAYROLL.PAY_PERIODS.PERIOD_NAME%TYPE,
        p_start_date  IN  DATE,
        p_end_date    IN  DATE,
        p_pay_date    IN  DATE,
        p_period_id   OUT PAYROLL.PAY_PERIODS.PERIOD_ID%TYPE,
        p_result      OUT NUMBER,
        p_message     OUT VARCHAR2
    );

    PROCEDURE generate_payslips(
        p_period_id  IN  PAYROLL.PAY_PERIODS.PERIOD_ID%TYPE,
        p_result     OUT NUMBER,
        p_message    OUT VARCHAR2
    );

    PROCEDURE approve_payslip(
        p_payslip_id IN  PAYROLL.PAYSLIPS.PAYSLIP_ID%TYPE,
        p_result     OUT NUMBER,
        p_message    OUT VARCHAR2
    );

    PROCEDURE close_pay_period(
        p_period_id  IN  PAYROLL.PAY_PERIODS.PERIOD_ID%TYPE,
        p_result     OUT NUMBER,
        p_message    OUT VARCHAR2
    );

    FUNCTION calc_tax(p_gross IN NUMBER) RETURN NUMBER;
    FUNCTION calc_insurance(p_gross IN NUMBER, p_rate IN NUMBER DEFAULT 0.08) RETURN NUMBER;

END PKG_PAYROLL;
/
