"""Candidate plans -> safety check -> ranking -> decision."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

from orchestrate_kit.trace import Trace

from .engine import Analysis
from .schemas import PaymentOption
from .spending import Change, find_changes, fmt_amount


@dataclass
class Plan:
    method: str                                  # full_payment | partial_payment | installments | wait
    payments: list[tuple[date, Decimal]]
    option: PaymentOption | None = None
    changes: tuple[Change, ...] = ()
    reason: str = ""

    @property
    def total(self) -> Decimal:
        return sum((a for _, a in self.payments), Decimal(0))

    @property
    def start(self) -> date:
        return self.payments[0][0]

    @property
    def end(self) -> date:
        return self.payments[-1][0]

    def rank_key(self, deadline: date):
        return (
            self.end > deadline,                  # 1. completes by desired_completion_date
            len(self.changes) > 0,                # 2. no spending changes
            self.total,                           # 3. lowest total paid
            self.start,                           # 4. starts earlier
            len(self.payments),                   # 5. fewer payments
            self.option.num if self.option else 0,  # 6. lowest payment_option_id
        )


@dataclass
class Decision:
    analysis: Analysis
    status: str
    method: str
    plan: Plan | None
    safe_today: Decimal
    earliest_full: date | None
    rejected: list[tuple[str, str]] = field(default_factory=list)

    @property
    def payment_plan(self) -> str:
        if self.plan is None:
            return "none"
        return "|".join(f"{d.isoformat()}:{fmt_amount(a)}" for d, a in self.plan.payments)

    @property
    def spending_changes(self) -> str:
        if self.plan is None or not self.plan.changes:
            return "none"
        return "|".join(c.code() for c in self.plan.changes)


def schedule(o: PaymentOption) -> list[tuple[date, Decimal]]:
    step = o.payment_frequency_days or 0
    return [(o.first_payment_date + timedelta(days=step * k), o.payment_amount) for k in range(o.number_of_payments)]


def floor2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def decide(a: Analysis, options: list[PaymentOption], trace: Trace) -> Decision:
    req, prof, fc = a.request, a.profile, a.forecast
    amt, today, deadline = req.requested_amount, req.request_date, req.desired_completion_date
    accepted = set(prof.payment_methods_user_will_consider)
    safe = floor2(a.safe_today)
    ef = a.earliest_full
    plans: list[Plan] = []
    rejected: list[tuple[str, str]] = []

    def consider(p: Plan, label: str) -> None:
        if p.end > deadline:
            rejected.append((label, f"finishes {p.end} after deadline {deadline}"))
        elif not fc.is_safe(p.payments):
            rejected.append((label, "breaks the minimum balance"))
        else:
            plans.append(p)

    # full payment today
    if "full_payment" in accepted:
        consider(Plan("full_payment", [(today, amt)]), "full_payment")
    else:
        rejected.append(("full_payment", "user does not consider full payment"))

    # supplied installment offers
    for o in sorted(options, key=lambda o: o.num):
        if o.payment_method != "installments":
            continue
        label = f"installments:{o.payment_option_id}"
        if "installments" not in accepted:
            rejected.append((label, "user does not consider installments"))
        elif prof.max_installment_months is None or o.number_of_payments > prof.max_installment_months:
            rejected.append((label, f"{o.number_of_payments} payments > max {prof.max_installment_months} months"))
        else:
            consider(Plan("installments", schedule(o), option=o), label)

    # partial now, rest on the earliest safe date
    if "partial_payment" in accepted and req.allows_partial_payment and Decimal(0) < safe < amt and ef and ef <= deadline:
        consider(Plan("partial_payment", [(today, safe), (ef, amt - safe)]), "partial_payment")
    elif "partial_payment" in accepted:
        rejected.append(("partial_payment", "not allowed by request" if not req.allows_partial_payment
                         else "no safe split completes by the deadline"))

    # wait for the earliest safe full-payment date
    if "full_payment" in accepted and ef and today < ef <= deadline:
        consider(Plan("wait", [(ef, amt)]), "wait")

    # permitted spending changes, only if nothing safe exists without them
    if not plans and "full_payment" in accepted:
        found = find_changes(fc, prof, [(today, amt)])
        if found:
            changes, _ = found
            plans.append(Plan("full_payment", [(today, amt)], changes=changes, reason="with spending changes"))
        else:
            rejected.append(("full_payment+changes", "no permitted set of up to 3 changes is enough"))
    if not plans and "installments" in accepted:
        for o in sorted(options, key=lambda o: o.num):
            if (o.payment_method == "installments" and prof.max_installment_months is not None
                    and o.number_of_payments <= prof.max_installment_months):
                p = Plan("installments", schedule(o), option=o)
                if p.end <= deadline:
                    found = find_changes(fc, prof, p.payments)
                    if found:
                        p.changes = found[0]
                        plans.append(p)

    for label, why in rejected:
        trace.step("rejected", plan=label, why=why)

    if not plans:
        return Decision(a, "not_affordable", "not_recommended", None, safe, ef, rejected)

    best = min(plans, key=lambda p: p.rank_key(deadline))
    trace.step("chosen", method=best.method, payments=best.payments,
               option=best.option.payment_option_id if best.option else None,
               changes=[c.code() for c in best.changes],
               alternatives=[(p.method, p.option.payment_option_id if p.option else None, p.total) for p in plans if p is not best])
    if best.method == "full_payment" and not best.changes:
        status = "affordable_now"
    elif best.method == "wait":
        status = "affordable_later"
    else:
        status = "affordable_with_plan"
    return Decision(a, status, best.method, best, safe, ef, rejected)
