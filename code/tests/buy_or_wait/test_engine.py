from datetime import date, timedelta
from decimal import Decimal as D

from orchestrate_kit.trace import Trace

from buy_or_wait.evidence.apply import UserEvidence, make_adjuster
from buy_or_wait.forecast import Flow, Forecast, build_forecast
from buy_or_wait.fx import FxTable
from buy_or_wait.ledger import CashEvent, Ledger
from buy_or_wait.recurrence import detect_series
from buy_or_wait.schemas import Message, Profile

START = date(2025, 1, 1)


def cash(eid, on, amt, direction="debit", cat="rent", typ="expense", desc="x", flex="fixed"):
    return CashEvent(eid, on, D(amt), direction, typ, cat, desc, flex, None, "settled")


def forecast(flows, opening="1000", minimum="500"):
    return Forecast(START, START + timedelta(days=90), D(opening), D(minimum), flows, [], intraday=True)


def test_headroom_is_lowest_future_balance_above_minimum():
    fc = forecast([Flow(date(2025, 1, 10), D(-300), "rent"), Flow(date(2025, 1, 15), D(1000), "salary")])
    assert fc.headroom_from(START) == D(200)            # 1000 - 300 - 500
    assert fc.earliest_full(D(600)) == date(2025, 1, 15)  # after salary lands


def test_payment_on_payday_lands_after_the_salary():
    fc = forecast([Flow(date(2025, 1, 15), D(1000), "salary")], opening="600")
    assert fc.is_safe([(date(2025, 1, 15), D(1000))])
    assert not fc.is_safe([(date(2025, 1, 14), D(1000))])


def test_monthly_series_detected_and_projected():
    hist = [cash(f"e{i}", date(2024, m, 4), "100") for i, m in enumerate(range(9, 13))]
    s = detect_series(hist, START)
    assert len(s) == 1 and s[0].monthly_dom == 4
    fc = build_forecast(Ledger(history=hist), D(1000), D(0), START)
    assert [f.on for f in fc.flows] == [date(2025, 1, 4), date(2025, 2, 4), date(2025, 3, 4)]  # window ends 1 Apr


def test_one_off_spike_does_not_inflate_a_series():
    hist = [cash(f"g{i}", START - timedelta(days=7 * (8 - i)), "100", cat="groceries") for i in range(8)]
    hist.append(cash("bulk", START - timedelta(days=1), "900", cat="groceries"))
    (s,) = detect_series(hist, START)
    assert s.amount == D(100)


def _profile():
    return Profile("u", "EUR", D(1000), D(500), (), (), (), (), ("full_payment",), None)


def _msg(mid):
    from datetime import datetime, timezone
    return Message(mid, "u", None, None, datetime(2024, 12, 30, tzinfo=timezone.utc), "financial_service", "...")


def test_scam_and_unconfirmed_income_facts_change_nothing():
    salary = [cash(f"s{i}", date(2024, m, 15), "2000", "credit", "salary", "income") for i, m in enumerate(range(9, 13))]
    base = build_forecast(Ledger(history=list(salary)), D(1000), D(500), START)
    facts = [
        (_msg("m1"), {"kind": "suspicious_request", "amount": "5000", "currency": "EUR", "date": "2025-01-02",
                      "percent": "", "scope": "", "category": "", "summary": "Pay the release fee to get EUR 5000"}),
        (_msg("m2"), {"kind": "income_not_confirmed", "amount": "3000", "currency": "EUR", "date": "2025-01-20",
                      "percent": "", "scope": "", "category": "bonus", "summary": "Bonus pending approval"}),
    ]
    ev = UserEvidence(facts=facts)
    adj = build_forecast(Ledger(history=list(salary)), D(1000), D(500), START,
                         adjust=make_adjuster(ev, _profile(), FxTable([]), Trace("t")))
    assert adj.headroom_from(START) == base.headroom_from(START)
    assert sorted((f.on, f.amount) for f in adj.flows) == sorted((f.on, f.amount) for f in base.flows)


def test_salary_raise_applies_from_effective_date():
    salary = [cash(f"s{i}", date(2024, m, 15), "2000", "credit", "salary", "income") for i, m in enumerate(range(9, 13))]
    ev = UserEvidence(facts=[(_msg("m"), {"kind": "salary_amount_change", "amount": "2500", "currency": "EUR",
                                          "date": "2025-02-15", "percent": "", "scope": "permanent",
                                          "category": "salary", "summary": ""})])
    fc = build_forecast(Ledger(history=salary), D(1000), D(500), START,
                        adjust=make_adjuster(ev, _profile(), FxTable([]), Trace("t")))
    pay = {f.on: f.amount for f in fc.flows if f.amount > 0}
    assert pay[date(2025, 1, 15)] == D(2000) and pay[date(2025, 2, 15)] == D(2500)
