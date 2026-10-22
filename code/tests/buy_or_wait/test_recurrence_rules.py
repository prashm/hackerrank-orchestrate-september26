"""Recurring-pattern detection and projection."""

from datetime import date, timedelta
from decimal import Decimal as D

from .conftest import REQ_DATE


def series(a, category):
    return [s for s in a.forecast.series if s.category == category]


def dates(a, category):
    return sorted(f.on for f in a.forecast.flows if f.series_key and f.series_key[1] == category)


def test_ten_day_cadence_is_one_fixed_series_not_three_monthly_ones(sc):
    sc.every(10, 50, last=date(2025, 5, 28), count=12, category="groceries")
    a = sc.analyze(10)
    (g,) = series(a, "groceries")
    assert g.period_days == 10
    assert dates(a, "groceries")[:3] == [date(2025, 6, 7), date(2025, 6, 17), date(2025, 6, 27)]


def test_base_pay_and_commission_on_different_days_are_separate_streams(sc):
    sc.salary("3000", day=15)
    sc.monthly(24, "1000", direction="credit", category="salary", typ="income", desc="Commission")
    a = sc.analyze(10)
    assert sorted(s.monthly_dom for s in series(a, "salary")) == [15, 24]


def test_one_off_spike_is_not_part_of_the_pattern(sc):
    sc.every(7, 100, last=date(2025, 5, 30), count=10, category="groceries")
    sc.event(date(2025, 6, 1), 900, category="groceries", desc="Bulk pantry purchase")
    (g,) = series(sc.analyze(10), "groceries")
    assert g.amount == D(100)


def test_late_payroll_moves_payday_forward(sc):
    sc.salary("3000", months=4)          # Feb..May 15
    sc.events.pop()                      # drop May 15 ...
    sc.event(date(2025, 5, 23), 3000, direction="credit", typ="income", category="salary")  # ... paid on the 23rd
    assert dates(sc.analyze(10), "salary")[0] == date(2025, 6, 23)


def test_final_payroll_ends_income(sc):
    sc.salary("3000", months=4)
    sc.events.pop()                      # May is the final payroll
    sc.event(date(2025, 5, 15), 3000, direction="credit", typ="income", category="salary",
             desc="Final employer payroll")
    assert dates(sc.analyze(10), "salary") == []


def test_income_that_missed_a_payment_has_stopped(sc):
    for m in (1, 2, 3):
        sc.event(date(2025, m, 20), 800, direction="credit", typ="income", category="salary")
    assert dates(sc.analyze(10), "salary") == []   # April and May never arrived


def test_regular_income_ignores_a_one_off_dip(sc):
    for m, amt in ((2, 3000), (3, 3000), (4, 3000), (5, 2100)):
        sc.event(date(2025, m, 15), amt, direction="credit", typ="income", category="salary")
    (s,) = series(sc.analyze(10), "salary")
    assert s.amount == D(3000)


def test_variable_income_is_projected_at_its_recent_minimum(sc):
    for m, amt in ((1, 520), (2, 480), (3, 610), (4, 455), (5, 590)):
        sc.event(date(2025, m, 7), amt, direction="credit", typ="income", category="salary", desc="Freelance")
    (s,) = series(sc.analyze(10), "salary")
    assert s.amount == D(455)


def test_ended_series_is_not_projected(sc):
    for m in (1, 2, 3):   # gym stopped after March
        sc.event(date(2025, m, 10), 40, category="gym", typ="subscription")
    assert series(sc.analyze(10), "gym") == []


def test_window_is_request_date_plus_90_days_inclusive(sc):
    sc.monthly(3, 100, category="rent")
    a = sc.analyze(10)
    assert a.forecast.end == REQ_DATE + timedelta(days=90)
    assert dates(a, "rent") == [date(2025, 6, 3), date(2025, 7, 3), date(2025, 8, 3)]


def test_committed_row_replaces_projection_only_when_it_is_the_same_payment(sc):
    sc.monthly(6, 120, category="insurance")
    # a scheduled top-up of a different size is an extra outflow, not this month's premium
    sc.event(date(2025, 6, 1), 80, category="insurance", status="scheduled", settles=date(2025, 6, 11))
    a = sc.analyze(10)
    june = sorted(a2 for d, a2 in ((f.on, f.amount) for f in a.forecast.flows) if d.month == 6)
    assert june == [D(-120), D(-80)]
    # the same premium arriving as a scheduled debit replaces the projected one
    sc.events.pop()
    sc.event(date(2025, 6, 1), 120, category="insurance", status="scheduled", settles=date(2025, 6, 6))
    a = sc.analyze(10)
    assert sorted((f.on, f.amount) for f in a.forecast.flows if f.on.month == 6) == [(date(2025, 6, 6), D(-120))]


def test_monthly_salary_and_weekly_payouts_in_one_category_are_both_projected(sc):
    sc.salary("1000", day=1)
    sc.every(7, [400, 380, 450, 300, 420, 390, 410, 360], last=date(2025, 5, 29),
             direction="credit", typ="income", category="salary", desc="Platform payout")
    a = sc.analyze(10)
    assert {(s.monthly_dom, s.period_days) for s in series(a, "salary")} == {(None, 7), (1, None)}


def test_foreign_currency_salary_is_projected_in_home_currency(sc):
    from buy_or_wait.schemas import Rate
    for m in (2, 3, 4, 5):
        sc.rates.append(Rate(date(2025, m, 15), "USD", "EUR", D("0.92")))
        sc.event(date(2025, m, 15), 1000, direction="credit", typ="income", category="salary", ccy="USD")
    (s,) = series(sc.analyze(10), "salary")
    assert s.amount == D("920.00")
