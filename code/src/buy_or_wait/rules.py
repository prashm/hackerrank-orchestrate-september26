"""Output-contract rules, plugged into orchestrate_kit.validate."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from orchestrate_kit.validate import RuleSet, matches, one_of

STATUSES = ("affordable_now", "affordable_with_plan", "affordable_later", "not_affordable")
METHODS = ("full_payment", "partial_payment", "installments", "wait", "not_recommended")
PLAN_RX = r"none|\d{4}-\d{2}-\d{2}:\d+(\.\d{1,2})?(\|\d{4}-\d{2}-\d{2}:\d+(\.\d{1,2})?)*"
CHANGES_RX = r"none|((stop:event_\d+|reduce_to:event_\d+:\d+(\.\d{1,2})?)(\|(stop:event_\d+|reduce_to:event_\d+:\d+(\.\d{1,2})?)){0,2})"

rules = RuleSet(id_column="request_id")
rules.add("status_enum", one_of("affordability_status", STATUSES))
rules.add("method_enum", one_of("recommended_payment_method", METHODS))
rules.add("plan_format", matches("payment_plan", PLAN_RX))
rules.add("changes_format", matches("spending_changes_needed", CHANGES_RX))
rules.add("earliest_format", matches("earliest_date_for_full_payment", r"\d{4}-\d{2}-\d{2}", allow_empty=True))


def _plan(row) -> list[tuple[date, Decimal]]:
    if row["payment_plan"] == "none":
        return []
    out = []
    for part in row["payment_plan"].split("|"):
        d, a = part.split(":")
        out.append((date.fromisoformat(d), Decimal(a)))
    return out


@rules.rule("safe_bounds")
def _bounds(row, ctx):
    x = Decimal(row["amount_safe_to_pay"])
    if not (0 <= x <= ctx["request"].requested_amount):
        return f"amount_safe_to_pay {x} outside [0, requested]"


@rules.rule("plan_chronological")
def _chrono(row, ctx):
    ds = [d for d, _ in _plan(row)]
    if ds != sorted(ds):
        return "payment_plan not chronological"


@rules.rule("method_status_consistency")
def _consistency(row, ctx):
    m, s = row["recommended_payment_method"], row["affordability_status"]
    ok = {
        "not_recommended": s == "not_affordable" and row["payment_plan"] == "none",
        "wait": s == "affordable_later",
        "partial_payment": s == "affordable_with_plan",
        "installments": s == "affordable_with_plan",
        "full_payment": s in ("affordable_now", "affordable_with_plan"),
    }[m]
    if not ok:
        return f"method {m} inconsistent with status {s}"
    if s == "affordable_now" and row["earliest_date_for_full_payment"] != ctx["request"].request_date.isoformat():
        return "affordable_now requires earliest_date_for_full_payment == request_date"


@rules.rule("plan_completes_request")
def _completes(row, ctx):
    req, plan = ctx["request"], _plan(row)
    m = row["recommended_payment_method"]
    if m in ("full_payment", "wait", "partial_payment") and sum(a for _, a in plan) != req.requested_amount:
        return "plan does not add up to requested_amount"
    if m == "partial_payment":
        if len(plan) != 2 or plan[0] != (req.request_date, Decimal(row["amount_safe_to_pay"])):
            return "partial plan must pay amount_safe_to_pay on request_date"
        if plan[1][0].isoformat() != row["earliest_date_for_full_payment"] or plan[1][0] > req.desired_completion_date:
            return "partial remainder must land on earliest date, by the deadline"
    if m == "installments":
        opts = [o for o in ctx["options"] if o.payment_method == "installments"]
        if not any(len(plan) == o.number_of_payments and plan[0][0] == o.first_payment_date
                   and all(a == o.payment_amount for _, a in plan) for o in opts):
            return "installment plan matches no supplied option"


@rules.rule("spending_changes_eligible")
def _changes(row, ctx):
    if row["spending_changes_needed"] == "none":
        return None
    prof, events = ctx["profile"], ctx["events"]
    msgs, seen = [], set()
    for part in row["spending_changes_needed"].split("|"):
        bits = part.split(":")
        e = events.get(bits[1])
        if e is None or e.user_id != prof.user_id:
            msgs.append(f"{bits[1]} is not this user's event")
            continue
        if e.event_id in seen:
            msgs.append(f"{e.event_id} changed twice")
        seen.add(e.event_id)
        if e.category in prof.expense_categories_to_protect:
            msgs.append(f"{e.event_id} is in a protected category")
        if bits[0] == "stop" and (e.flexibility not in ("stoppable", "reducible_or_stoppable")
                                  or e.category not in prof.expense_categories_user_is_willing_to_stop):
            msgs.append(f"{e.event_id} cannot be stopped")
        if bits[0] == "reduce_to":
            if (e.flexibility not in ("reducible", "reducible_or_stoppable")
                    or e.category not in prof.expense_categories_user_is_willing_to_reduce):
                msgs.append(f"{e.event_id} cannot be reduced")
            elif e.minimum_allowed_amount is not None and Decimal(bits[2]) < e.minimum_allowed_amount:
                msgs.append(f"{e.event_id} reduced below its minimum")
    return msgs
