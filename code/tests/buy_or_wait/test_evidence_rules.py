"""What message/image facts do to the forecast (applied deterministically)."""

from datetime import date
from decimal import Decimal as D

from buy_or_wait.schemas import ImageRef

from .conftest import REQ_DATE


def pay(a):
    return sorted((f.on, f.amount) for f in a.forecast.flows if f.amount > 0)


def test_raise_applies_from_its_effective_date(sc):
    sc.salary("3000")
    sc.message(facts=[dict(kind="salary_amount_change", amount=3500, currency="EUR", date="2025-07-15",
                           scope="permanent")])
    assert pay(sc.analyze(10)) == [(date(2025, 6, 15), D(3000)), (date(2025, 7, 15), D(3500)),
                                   (date(2025, 8, 15), D(3500))]


def test_temporary_pay_applies_to_the_next_payroll_only(sc):
    for m, amt in ((2, 3000), (3, 3000), (4, 2000), (5, 2000)):
        sc.event(date(2025, m, 15), amt, direction="credit", typ="income", category="salary")
    sc.message(facts=[dict(kind="salary_amount_change", amount=2000, currency="EUR", scope="next_payment_only")])
    assert [a for _, a in pay(sc.analyze(10))] == [D(2000), D(3000), D(3000)]


def test_moved_payday_shifts_the_next_salary(sc):
    sc.salary("3000")
    sc.message(facts=[dict(kind="salary_date_change", date="2025-06-23")])
    assert [d for d, _ in pay(sc.analyze(10))] == [date(2025, 6, 23), date(2025, 7, 23), date(2025, 8, 23)]


def test_first_salary_at_a_new_job_starts_monthly_income(sc):
    sc.message(facts=[dict(kind="salary_confirmed", amount=2800, currency="EUR", date="2025-06-15")])
    assert pay(sc.analyze(10)) == [(date(2025, 6, 15), D(2800)), (date(2025, 7, 15), D(2800)),
                                   (date(2025, 8, 15), D(2800))]


def test_ended_employment_removes_all_income_including_scheduled(sc):
    sc.salary("3000")
    sc.event(date(2025, 6, 1), 3000, direction="credit", typ="income", category="salary",
             status="scheduled", settles=date(2025, 6, 15), desc="Next confirmed salary")
    sc.message(facts=[dict(kind="income_ended")])
    assert pay(sc.analyze(10)) == []


def test_one_ended_household_income_keeps_the_remaining_one(sc):
    sc.salary("3000", day=15)
    sc.salary("1200", day=20)
    sc.message(facts=[dict(kind="income_ended"), dict(kind="salary_confirmed", amount=1200, currency="EUR")])
    assert {a for _, a in pay(sc.analyze(10))} == {D(1200)}


def test_base_only_drops_commission_and_trusts_settled_base(sc):
    sc.salary("3000", day=15)
    for m, amt in ((2, 900), (3, 1400), (4, 700), (5, 1100)):
        sc.event(date(2025, m, 24), amt, direction="credit", typ="income", category="salary", desc="Commission")
    sc.message(facts=[dict(kind="base_salary_only", amount=5000, currency="EUR")])
    assert {a for _, a in pay(sc.analyze(10))} == {D(3000)}


def test_pending_platform_payout_drops_variable_gig_income_but_not_salary(sc):
    sc.salary("1000", day=1)
    sc.every(7, [400, 380, 450, 300, 420, 390, 410, 360], last=date(2025, 5, 29),
             direction="credit", typ="income", category="salary", desc="Platform payout")
    sc.message(source="service_provider",
               facts=[dict(kind="income_not_confirmed", summary="Next platform payout is still pending")])
    assert {a for _, a in pay(sc.analyze(10))} == {D(1000)}


def test_approved_invoice_replaces_variable_freelance_projection(sc):
    for m, amt in ((1, 700), (2, 1300), (3, 900), (4, 860), (5, 1100)):
        sc.event(date(2025, m, 8), amt, direction="credit", typ="income", category="salary", desc="Invoice payment")
    sc.message(source="service_provider",
               facts=[dict(kind="one_time_income_confirmed", amount=2000, currency="EUR", date="2025-06-15")])
    assert pay(sc.analyze(10)) == [(date(2025, 6, 15), D(2000))]


def test_rent_increase_applies_from_the_next_payment(sc):
    sc.monthly(1, 1000, category="rent")
    sc.message(source="service_provider", facts=[dict(kind="expense_change", category="rent", percent=12)])
    rent = sorted(f.amount for f in sc.analyze(10).forecast.flows)
    assert rent == [D(-1120)] * 3


def test_scam_and_pending_prize_change_nothing(sc):
    sc.salary("3000")
    base = pay(sc.analyze(10))
    sc.message(source="financial_service",
               facts=[dict(kind="suspicious_request", amount=5000, summary="Pay the release charge today"),
                      dict(kind="income_not_confirmed", amount=8000, summary="Prize still in processing")])
    assert pay(sc.analyze(10)) == base


def test_payslip_image_matching_a_settled_salary_is_a_duplicate(sc):
    sc.salary("3000")
    slip = sc.event(date(2025, 5, 31), None, direction="credit", typ="income", category="salary",
                    desc="May net salary")
    sc.images.append(ImageRef("image_01", "user_1", "request_1", slip))
    sc.image_ext["image_01"] = {"amount": "3000", "currency": "EUR", "amount_label": "Net Pay"}
    a = sc.analyze(10)
    assert slip in {eid for eid, _ in a.ledger.excluded}
    assert [d for d, _ in pay(a)] == [date(2025, 6, 15), date(2025, 7, 15), date(2025, 8, 15)]


def test_messages_sent_after_the_request_are_ignored(sc):
    from datetime import datetime, timezone
    sc.salary("3000")
    sc.message(sent=datetime(2025, 6, 20, tzinfo=timezone.utc), facts=[dict(kind="income_ended")])
    assert len(pay(sc.analyze(10))) == 3


def test_fact_amounts_in_foreign_currency_are_converted(sc):
    from buy_or_wait.schemas import Rate
    sc.rates.append(Rate(date(2025, 6, 15), "USD", "EUR", D("0.9")))
    sc.message(facts=[dict(kind="salary_confirmed", amount=1000, currency="USD", date="2025-06-15")])
    assert pay(sc.analyze(10))[0] == (date(2025, 6, 15), D("900.0"))
