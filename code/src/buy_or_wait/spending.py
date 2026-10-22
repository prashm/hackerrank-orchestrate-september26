"""Search for the smallest set of permitted spending changes that makes a plan safe."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import combinations

from .forecast import Forecast, Flow
from .recurrence import Series
from .schemas import Profile

STOPPABLE = {"stoppable", "reducible_or_stoppable"}
REDUCIBLE = {"reducible", "reducible_or_stoppable"}
MAX_CHANGES = 3


@dataclass(frozen=True)
class Change:
    action: str              # 'stop' | 'reduce_to'
    series: Series
    new_amount: Decimal | None = None

    @property
    def event_id(self) -> str:
        return self.series.last.event_id

    def code(self) -> str:
        if self.action == "stop":
            return f"stop:{self.event_id}"
        return f"reduce_to:{self.event_id}:{fmt_amount(self.new_amount)}"

    def saving_per_occurrence(self) -> Decimal:
        return self.series.amount if self.action == "stop" else self.series.amount - self.new_amount


def fmt_amount(x: Decimal) -> str:
    x = x.quantize(Decimal("0.01"))
    return str(x.quantize(Decimal(1))) if x == x.to_integral_value() else f"{x:.2f}"


def candidate_changes(fc: Forecast, profile: Profile) -> list[Change]:
    protected = set(profile.expense_categories_to_protect)
    can_stop = set(profile.expense_categories_user_is_willing_to_stop)
    can_reduce = set(profile.expense_categories_user_is_willing_to_reduce)
    out: list[Change] = []
    for s in fc.series:
        if s.direction != "debit" or not s.members or not s.occurrences or s.category in protected:
            continue
        if s.flexibility in STOPPABLE and s.category in can_stop:
            out.append(Change("stop", s))
        floor = s.minimum_allowed_amount
        if s.flexibility in REDUCIBLE and s.category in can_reduce and floor is not None and floor < s.amount:
            out.append(Change("reduce_to", s, floor))
    return out


def apply_changes(fc: Forecast, changes: tuple[Change, ...]) -> Forecast:
    by_key = {id(c.series): c for c in changes}
    flows: list[Flow] = []
    for f in fc.flows:
        hit = next((c for c in changes if f.series_key == c.series.key and f.source == c.series.describe()), None)
        if hit is None:
            flows.append(f)
        elif hit.action == "reduce_to":
            flows.append(Flow(f.on, -hit.new_amount, f.source + " (reduced)", f.series_key, f.kind))
    return fc.with_flows(flows)


def find_changes(fc: Forecast, profile: Profile, payments: list[tuple[date, Decimal]]) -> tuple[tuple[Change, ...], Forecast] | None:
    """Fewest changes that make `payments` safe; ties -> smallest total saving needed."""
    cands = candidate_changes(fc, profile)
    for k in range(1, MAX_CHANGES + 1):
        feasible = []
        for combo in combinations(cands, k):
            if len({id(c.series) for c in combo}) < k:
                continue  # stop and reduce on the same event are mutually exclusive
            adj = apply_changes(fc, combo)
            if adj.is_safe(payments):
                cut = sum(c.saving_per_occurrence() * len(c.series.occurrences) for c in combo)
                feasible.append((cut, [c.code() for c in combo], combo, adj))
        if feasible:
            feasible.sort(key=lambda t: (t[0], t[1]))
            return feasible[0][2], feasible[0][3]
    return None
