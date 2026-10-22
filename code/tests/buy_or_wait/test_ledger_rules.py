"""Cash-state rules: which records count, when, and in which currency."""

from datetime import date
from decimal import Decimal as D

from buy_or_wait.schemas import Rate

from .conftest import REQ_DATE, fact

# Scenario default: balance 5000, minimum 1000 -> 4000 headroom with no other flows.


def flows(a):
    return sorted((f.on, f.amount) for f in a.forecast.flows)


def test_failed_and_cancelled_records_are_ignored(sc):
    sc.event(date(2025, 6, 1), 500, status="failed", settles=date(2025, 6, 4))
    sc.event(date(2025, 6, 1), 700, status="cancelled", settles=date(2025, 6, 5))
    assert sc.analyze(100).forecast.headroom_from(REQ_DATE) == D(4000)


def test_pending_debit_is_reserved_on_its_settlement_date(sc):
    sc.event(date(2025, 6, 1), 300, status="pending", settles=date(2025, 6, 5), category="healthcare")
    a = sc.analyze(100)
    assert flows(a) == [(date(2025, 6, 5), D(-300))]
    assert a.forecast.headroom_from(REQ_DATE) == D(3700)


def test_overdue_pending_debit_is_reserved_today(sc):
    sc.event(date(2025, 5, 28), 300, status="pending", settles=date(2025, 6, 1), category="healthcare")
    assert flows(sc.analyze(100)) == [(REQ_DATE, D(-300))]


def test_pending_credits_and_refunds_are_not_counted(sc):
    orig = sc.event(date(2025, 5, 20), 200, category="shopping")
    sc.event(date(2025, 6, 1), 200, direction="credit", typ="refund", category="shopping",
             status="pending", settles=date(2025, 6, 10), linked=orig)
    sc.event(date(2025, 6, 1), 900, direction="credit", typ="income", category="salary",
             status="pending", settles=date(2025, 6, 10), desc="Quarterly bonus")
    assert flows(sc.analyze(100)) == []


def test_scheduled_confirmed_salary_counts_and_repeats_monthly(sc):
    sc.event(date(2025, 6, 1), 2500, direction="credit", typ="income", category="salary",
             status="scheduled", settles=date(2025, 6, 15), desc="Next confirmed salary")
    credits = [(d, a) for d, a in flows(sc.analyze(100)) if a > 0]
    assert credits == [(date(2025, 6, 15), D(2500)), (date(2025, 7, 15), D(2500)), (date(2025, 8, 15), D(2500))]


def test_possible_duplicate_charge_is_ignored_unless_dispute_is_open(sc):
    orig = sc.event(date(2025, 5, 25), 400, category="shopping", desc="Original card charge")
    dup = sc.event(date(2025, 6, 1), 400, category="shopping", status="pending", settles=date(2025, 6, 6),
                   desc="Possible duplicate card charge", linked=orig)
    assert flows(sc.analyze(100)) == []
    sc.message(related=dup, source="bank", facts=[dict(kind="charge_disputed_open", amount=400)])
    assert flows(sc.analyze(100)) == [(date(2025, 6, 6), D(-400))]


def test_unrealized_investment_value_is_never_cash(sc):
    buy = sc.event(date(2025, 3, 1), 1000, typ="investment_purchase", category="investment")
    sc.event(date(2025, 6, 1), 1500, direction="non_cash", typ="investment_valuation", category="investment",
             status="unrealized", settles=None, linked=buy)
    assert flows(sc.analyze(100)) == []


def test_foreign_currency_uses_the_settlement_date_rate_in_stated_direction(sc):
    sc.rates += [Rate(date(2025, 6, 5), "USD", "EUR", D("0.9")), Rate(date(2025, 6, 6), "EUR", "USD", D("1.25"))]
    sc.event(date(2025, 6, 1), 100, ccy="USD", status="pending", settles=date(2025, 6, 5), category="travel")
    sc.event(date(2025, 6, 1), 100, ccy="USD", status="pending", settles=date(2025, 6, 6), category="travel")
    assert flows(sc.analyze(100)) == [(date(2025, 6, 5), D("-90.0")), (date(2025, 6, 6), D(-80))]


def test_blank_amount_is_never_zero_and_comes_from_the_image(sc):
    bill = sc.event(date(2025, 6, 1), None, category="utilities", status="pending", settles=date(2025, 6, 9),
                    desc="Outstanding telecom bill")
    a = sc.analyze(100)
    assert a.ledger.unresolved_amounts == [bill] and "unresolved_blank_amount" in a.trace.flags
    from buy_or_wait.schemas import ImageRef
    sc.images.append(ImageRef("image_01", "user_1", "request_1", bill))
    sc.image_ext["image_01"] = {"amount": "704.05", "currency": "EUR", "amount_label": "Amount due"}
    assert flows(sc.analyze(100)) == [(date(2025, 6, 9), D("-704.05"))]


def test_cancelled_authorization_followed_by_settled_purchase_counts_once(sc):
    auth = sc.event(date(2025, 5, 20), 66, category="shopping", status="cancelled", desc="Card authorization")
    sc.event(date(2025, 5, 21), 66, category="shopping", linked=auth, desc="Settled card purchase")
    a = sc.analyze(100)
    assert [c.event_id for c in a.ledger.history] == ["event_2"]


def test_internal_transfer_pair_is_excluded_from_history(sc):
    out = sc.event(date(2025, 5, 20), 500, category="transfer", desc="Transfer out")
    inn = sc.event(date(2025, 5, 21), 500, direction="credit", typ="income", category="transfer", desc="Transfer in")
    sc.message(source="bank", facts=[dict(kind="internal_transfer")])
    a = sc.analyze(100)
    assert {out, inn} <= {eid for eid, _ in a.ledger.excluded}
