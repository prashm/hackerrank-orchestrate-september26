"""Grounded, template-based explanations (no LLM tokens)."""

from __future__ import annotations

from decimal import Decimal

from orchestrate_kit.timeutil import human_date

from .planner import Decision


def money(ccy: str, x: Decimal) -> str:
    x = x.quantize(Decimal("0.01"))
    if x == x.to_integral_value():
        return f"{ccy} {int(x):,}"
    return f"{ccy} {x:,.2f}"


def _change_phrase(changes, ccy: str) -> str:
    parts = []
    for c in changes:
        name = c.series.last.description[0].lower() + c.series.last.description[1:]
        if c.action == "stop":
            parts.append(f"stop the {name}")
        else:
            parts.append(f"reduce the {name} to {money(ccy, c.new_amount)}")
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return text[0].upper() + text[1:]


def _pressure(d: Decision) -> str:
    """One grounded sentence about what limits the decision."""
    fc = d.analysis.forecast
    low_day, low_bal = fc.lowest_point()
    ccy = d.analysis.profile.home_currency
    income = sorted({f.on for f in fc.flows if f.amount > 0})
    nxt = next((x for x in income if x >= d.analysis.request.request_date), None)
    s = f"Projected balance is tightest on {human_date(low_day)} at {money(ccy, low_bal)}"
    if nxt:
        s += f"; next confirmed income is {human_date(nxt)}"
    return s + "."


def explain(d: Decision) -> str:
    a = d.analysis
    ccy = a.profile.home_currency
    req = a.request
    amt, minimum = req.requested_amount, a.profile.minimum_balance_to_keep
    p = d.plan
    if p is None:
        if d.safe_today > 0 and req.allows_partial_payment:
            head = (f"Do not proceed with the {money(ccy, amt)} request. Although {money(ccy, d.safe_today)} is "
                    f"available today, the full amount cannot be completed safely within 90 days.")
        else:
            head = (f"Do not make this payment by {human_date(req.desired_completion_date)}. None of the available "
                    f"options keeps the {money(ccy, minimum)} minimum protected.")
        return f"{head} {_pressure(d)}"
    if p.method == "full_payment" and p.changes:
        return (f"{_change_phrase(p.changes, ccy)}, then pay {money(ccy, amt)} today. "
                f"This leaves at least {money(ccy, minimum)} available.")
    if p.method == "full_payment":
        return f"Pay {money(ccy, amt)} today. This leaves at least {money(ccy, minimum)} available over the next 90 days."
    if p.method == "installments":
        head = (f"Use {len(p.payments)} installments of {money(ccy, p.payments[0][1])}, starting {human_date(p.start)}.")
        if p.changes:
            head = f"{_change_phrase(p.changes, ccy)}, then use {len(p.payments)} installments of {money(ccy, p.payments[0][1])}, starting {human_date(p.start)}."
        return f"{head} This leaves at least {money(ccy, minimum)} available."
    if p.method == "partial_payment":
        first, second = p.payments
        return (f"Pay {money(ccy, first[1])} today and the remaining {money(ccy, second[1])} on {human_date(second[0])}. "
                f"This completes the full request and keeps the {money(ccy, minimum)} minimum protected.")
    # wait
    return (f"Pay {money(ccy, amt)} in full on {human_date(p.start)}. Paying earlier would take the balance below "
            f"the {money(ccy, minimum)} minimum.")
