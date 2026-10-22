"""Plan eligibility, safety, ranking and the resulting output fields."""

from datetime import date, timedelta
from decimal import Decimal as D

from buy_or_wait.cli import to_row
from buy_or_wait.rules import rules

from .conftest import REQ_DATE

DEADLINE = date(2025, 7, 31)


def row(d):
    return to_row(d)


def tight(sc):
    """Balance 3000 / minimum 1000; rent 1500 on the 5th; salary 3000 on the 15th.
    Lowest point before payday is 1500 (500 spare); from payday on the low is 3000 (2000 spare)."""
    sc.balance, sc.minimum = "3000", "1000"
    sc.salary("3000")
    sc.monthly(5, 1500, category="rent")
    return sc


def test_affordable_now(sc):
    d = sc.decide(500, deadline=DEADLINE)
    r = row(d)
    assert (r["affordability_status"], r["recommended_payment_method"]) == ("affordable_now", "full_payment")
    assert r["payment_plan"] == "2025-06-03:500" and r["earliest_date_for_full_payment"] == "2025-06-03"


def test_safe_amount_is_capped_at_the_request(sc):
    assert row(sc.decide(500))["amount_safe_to_pay"] == "500"


def test_wait_until_payday_when_full_payment_is_accepted(sc):
    tight(sc)
    sc.methods = ("full_payment",)
    r = row(sc.decide(1200, deadline=DEADLINE))
    assert (r["affordability_status"], r["recommended_payment_method"]) == ("affordable_later", "wait")
    assert r["payment_plan"] == "2025-06-15:1200" and r["amount_safe_to_pay"] == "500"


def test_partial_payment_beats_wait_because_it_starts_earlier(sc):
    tight(sc)
    assert row(sc.decide(1200, deadline=DEADLINE))["recommended_payment_method"] == "partial_payment"


def test_wait_beats_installments_with_a_fee(sc):
    tight(sc)
    sc.methods = ("full_payment", "installments")
    sc.option("installments", 420, 3, date(2025, 6, 10), 30)  # 1260 total > 1200
    assert row(sc.decide(1200, deadline=DEADLINE))["recommended_payment_method"] == "wait"


def test_installments_when_user_will_not_pay_in_full(sc):
    sc.methods = ("installments",)
    sc.option("installments", 200, 3, date(2025, 6, 10), 30)
    r = row(sc.decide(600, deadline=date(2025, 8, 31)))
    assert r["recommended_payment_method"] == "installments"
    assert r["payment_plan"] == "2025-06-10:200|2025-07-10:200|2025-08-09:200"
    assert r["earliest_date_for_full_payment"] == "2025-06-03"   # capacity is independent of preference


def test_installments_rejected_beyond_max_months_or_deadline_or_when_not_considered(sc):
    sc.methods = ("installments",)
    sc.max_months = 2
    sc.option("installments", 200, 3, date(2025, 6, 10), 30)
    assert row(sc.decide(600, deadline=date(2025, 12, 31)))["recommended_payment_method"] == "not_recommended"
    sc.max_months = 6
    assert row(sc.decide(600, deadline=date(2025, 7, 15)))["recommended_payment_method"] == "not_recommended"
    sc.max_months = None
    assert row(sc.decide(600, deadline=date(2025, 12, 31)))["recommended_payment_method"] == "not_recommended"


def test_installment_option_ties_break_on_lowest_option_id(sc):
    sc.methods = ("installments",)
    sc.option("installments", 200, 3, date(2025, 6, 10), 30)
    sc.option("installments", 200, 3, date(2025, 6, 10), 30)
    d = sc.decide(600, deadline=date(2025, 8, 31))
    assert d.plan.option.payment_option_id == "payment_option_01"


def test_partial_payment_pays_safe_amount_now_and_rest_on_earliest_date(sc):
    tight(sc)
    sc.methods = ("partial_payment",)
    r = row(sc.decide(1200, deadline=DEADLINE))
    assert r["recommended_payment_method"] == "partial_payment" and r["affordability_status"] == "affordable_with_plan"
    assert r["payment_plan"] == "2025-06-03:500|2025-06-15:700"


def test_partial_payment_needs_request_permission(sc):
    tight(sc)
    sc.methods = ("partial_payment",)
    assert row(sc.decide(1200, deadline=DEADLINE, partial=False))["recommended_payment_method"] == "not_recommended"


def test_spending_change_makes_full_payment_safe_today(sc):
    sc.balance, sc.minimum = "1600", "1000"
    sc.salary("3000")
    stream = sc.monthly(10, 100, category="streaming", typ="subscription", flex="stoppable")
    r = row(sc.decide(600, deadline=date(2025, 6, 10)))   # deadline before payday -> no wait
    assert r["affordability_status"] == "affordable_with_plan" and r["recommended_payment_method"] == "full_payment"
    assert r["spending_changes_needed"] == f"stop:{stream[-1]}"
    assert r["amount_safe_to_pay"] == "500"                 # before optional changes


def test_reduce_goes_to_the_minimum_allowed_and_protected_categories_are_never_touched(sc):
    sc.balance, sc.minimum = "1600", "1000"
    sc.salary("3000")
    sc.monthly(9, 200, category="groceries", flex="reducible", min_allowed=50)       # protected
    dining = sc.monthly(10, 300, category="dining", flex="reducible", min_allowed=120)
    # spare before payday: 1600 - 200 - 300 - 1000 = 100; reducing dining frees 180
    r = row(sc.decide(250, deadline=date(2025, 6, 10)))
    assert r["spending_changes_needed"] == f"reduce_to:{dining[-1]}:120"


def test_no_safe_plan_is_not_recommended_with_no_payment(sc):
    sc.balance, sc.minimum = "1500", "1000"
    r = row(sc.decide(5000, deadline=DEADLINE))
    assert (r["affordability_status"], r["recommended_payment_method"], r["payment_plan"]) == \
        ("not_affordable", "not_recommended", "none")
    assert r["earliest_date_for_full_payment"] == ""


def test_every_row_passes_the_output_contract(sc):
    tight(sc)
    sc.option("installments", 420, 3, date(2025, 6, 10), 30)
    req = sc.request(1200, deadline=DEADLINE)
    for methods in [("full_payment",), ("installments",), ("partial_payment",), ("full_payment", "installments")]:
        sc.methods = methods
        d = sc.decide(1200, deadline=DEADLINE)
        ctx = {"request": req, "options": sc.options, "profile": d.analysis.profile,
               "events": {e.event_id: e for e in sc.events}}
        assert rules.check_row(row(d), ctx) == []
