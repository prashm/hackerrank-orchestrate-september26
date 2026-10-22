"""Detect recurring series in settled history and project them forward."""

from __future__ import annotations

import calendar
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from .ledger import CashEvent


DISCRETIONARY = {"dining", "shopping", "entertainment"}


@dataclass(frozen=True)
class RecurrenceConfig:
    min_occurrences: int = 3
    dom_tolerance: int = 2             # monthly: day-of-month slack
    gap_tolerance: int = 1             # fixed period: day slack
    min_period_share: float = 0.6      # share of gaps that must match the period
    lookback: int = 6                  # occurrences used for the amount estimate
    debit_estimator: str = "p25"       # mean | max | last | median | pN | mean+X  (calibrated on samples)
    discretionary_estimator: str = ""  # estimator for discretionary categories ('' = same as debit_estimator)
    credit_estimator: str = "min"      # min | mean | last | median
    outlier_factor: float = 2.5        # drop amounts above factor x median from a series
    missed_income_grace: int = 5       # days past an expected income date before it counts as missed
    stale_factor: float = 2.0          # series ended if last + stale_factor*period < as_of


@dataclass
class Series:
    key: tuple[str, str, str]                 # (event_type, category, direction)
    members: list[CashEvent]
    monthly_dom: int | None = None            # monthly on this day-of-month
    period_days: int | None = None            # or every N days
    amount: Decimal = Decimal(0)
    occurrences: list[date] = field(default_factory=list)
    # evidence-driven adjustments
    next_date: date | None = None                      # next occurrence moved here; cadence follows its day
    next_amount: Decimal | None = None                 # next occurrence only
    changes: list[tuple[date, Decimal]] = field(default_factory=list)  # permanent amount from date
    notes: list[str] = field(default_factory=list)

    def amount_for(self, i: int, d: date) -> Decimal:
        if i == 0 and self.next_amount is not None:
            return self.next_amount
        amt = self.amount
        for since, a in sorted(self.changes):
            if d >= since:
                amt = a
        return amt

    @property
    def direction(self) -> str:
        return self.key[2]

    @property
    def category(self) -> str:
        return self.key[1]

    @property
    def last(self) -> CashEvent:
        return self.members[-1]

    @property
    def flexibility(self) -> str:
        return self.last.flexibility

    @property
    def minimum_allowed_amount(self) -> Decimal | None:
        return self.last.minimum_allowed_amount

    @property
    def is_fixed_amount(self) -> bool:
        return len({m.amount for m in self.members[-3:]}) == 1

    @property
    def is_regular_amount(self) -> bool:
        """Some amount repeats exactly (salary), unlike gig payouts or commissions."""
        recent = [m.amount for m in self.members[-4:]]
        return len(set(recent)) < len(recent)

    def describe(self) -> str:
        cadence = f"monthly@{self.monthly_dom}" if self.monthly_dom else f"every {self.period_days}d"
        last = f"last={self.last.on} ({self.last.event_id})" if self.members else "from evidence"
        nxt = f" next={self.next_date}" if self.next_date else ""
        return f"{self.category}/{self.key[0]} {self.direction} {cadence} ~{self.amount.quantize(Decimal('0.01'))} {last}{nxt}"


def _add_months(d: date, months: int, dom: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(dom, calendar.monthrange(y, m)[1]))


def _estimate(amounts: list[Decimal], how: str) -> Decimal:
    if how == "max":
        return max(amounts)
    if how == "min":
        return min(amounts)
    if how == "last":
        return amounts[-1]
    if how == "median":
        return Decimal(str(median(amounts)))
    if how == "mean":
        return sum(amounts) / len(amounts)
    if how.startswith("mean+"):     # e.g. mean+10 -> mean * 1.10
        return sum(amounts) / len(amounts) * (1 + Decimal(how[5:]) / 100)
    if how.startswith("p"):         # percentile, e.g. p75
        xs = sorted(amounts)
        q = Decimal(how[1:]) / 100 * (len(xs) - 1)
        lo = int(q)
        hi = min(lo + 1, len(xs) - 1)
        return xs[lo] + (xs[hi] - xs[lo]) * (q - lo)
    raise ValueError(how)


def _monthly_cluster(members: list[CashEvent], dom: int, cfg: RecurrenceConfig, attach_late: bool) -> list[CashEvent] | None:
    picked = [m for m in members if abs(m.on.day - dom) <= cfg.dom_tolerance]
    # at most one per month, consecutive months
    by_month: dict[tuple[int, int], CashEvent] = {}
    for m in picked:
        by_month.setdefault((m.on.year, m.on.month), m)
    picked = sorted(by_month.values(), key=lambda m: m.on)
    # a late or moved latest occurrence (e.g. payroll settled a week late) still
    # belongs to the series and becomes its new anchor
    latest = members[-1]
    if attach_late and picked and latest.on > picked[-1].on and (latest.on.year, latest.on.month) not in by_month:
        months = (latest.on.year - picked[-1].on.year) * 12 + latest.on.month - picked[-1].on.month
        if months == 1 and latest.amount == picked[-1].amount:
            picked.append(latest)
    if len(picked) < cfg.min_occurrences:
        return None
    gaps = [(b.on.year - a.on.year) * 12 + b.on.month - a.on.month for a, b in zip(picked, picked[1:])]
    if sum(g == 1 for g in gaps) / len(gaps) < cfg.min_period_share:
        return None
    return picked


def _monthly(members: list[CashEvent], cfg: RecurrenceConfig) -> list[tuple[int, list[CashEvent]]]:
    """Every monthly cluster in the group (e.g. base pay on the 15th AND commission on the 24th)."""
    out: list[tuple[int, list[CashEvent]]] = []
    used: set[str] = set()
    for rank, (dom, _) in enumerate(Counter(m.on.day for m in members).most_common()):
        rest = [m for m in members if m.event_id not in used]
        if not rest:
            break
        picked = _monthly_cluster(rest, dom, cfg, attach_late=(rank == 0))
        if picked:
            out.append((dom, picked))
            used.update(m.event_id for m in picked)
    return out


def _fixed(members: list[CashEvent], cfg: RecurrenceConfig) -> tuple[int, list[CashEvent]] | None:
    if len(members) < cfg.min_occurrences:
        return None
    # a cadence chain only links payments of a similar size (a monthly salary that happens to
    # fall on a weekly payout date is not a payout)
    med = median(m.amount for m in members)
    members = [m for m in members if med / 2 <= m.amount <= med * 2]
    if len(members) < cfg.min_occurrences:
        return None
    gaps = [(b.on - a.on).days for a, b in zip(members, members[1:])]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return None
    period, _ = Counter(gaps).most_common(1)[0]
    if period >= 27:  # monthly cadence is handled by _monthly
        return None
    share = sum(abs(g - period) <= cfg.gap_tolerance for g in gaps) / len(gaps)
    if share < cfg.min_period_share:
        return None
    # chain backwards from the latest member
    chain = [members[-1]]
    for m in reversed(members[:-1]):
        gap = (chain[-1].on - m.on).days
        if abs(gap - period) <= cfg.gap_tolerance + 3:
            chain.append(m)
        elif gap > period + cfg.gap_tolerance + 3:
            break
    chain.reverse()
    if len(chain) < cfg.min_occurrences:
        return None
    return period, chain


def detect_series(history: list[CashEvent], as_of: date, cfg: RecurrenceConfig = RecurrenceConfig()) -> list[Series]:
    groups: dict[tuple[str, str, str], list[CashEvent]] = {}
    for ev in sorted(history, key=lambda e: e.on):
        groups.setdefault((ev.event_type, ev.category, ev.direction), []).append(ev)

    out: list[Series] = []
    for key, members in groups.items():
        if key[0] in ("refund", "investment_sale"):
            continue
        if len(members) >= 4:
            # one-off spikes (a bulk shop, an annual fee) are not part of the pattern
            med = median(m.amount for m in members)
            members = [m for m in members if m.amount <= med * Decimal(str(cfg.outlier_factor))]
        # a category can hold several streams (monthly salary + weekly gig payouts):
        # take the best explanation, then look again at what it left over
        found: list[tuple[Series, int]] = []
        rest = members
        while len(rest) >= cfg.min_occurrences:
            mon = _monthly(rest, cfg)
            fix = _fixed(rest, cfg)
            # a clean fixed cadence (e.g. every 10 days) beats day-of-month clusters that only
            # look monthly because 3 x 10 days ~ a month
            if mon and (not fix or sum(len(p) for _, p in mon) > len(fix[1])):
                batch = [(Series(key, picked, monthly_dom=dom), 30) for dom, picked in mon]
            elif fix:
                batch = [(Series(key, fix[1], period_days=fix[0]), fix[0])]
            else:
                break
            found += batch
            used = {m.event_id for s, _ in batch for m in s.members}
            rest = [m for m in rest if m.event_id not in used]
        for s, period in found:
            if s.last.on + timedelta(days=int(period * cfg.stale_factor)) < as_of:
                continue  # series has ended
            if s.direction == "credit" and "final" in s.last.description.lower():
                continue  # last payroll was the final one
            if s.direction == "credit" and s.last.status == "settled" and overdue(s, as_of - timedelta(days=cfg.missed_income_grace)):
                continue  # an expected payment never arrived: income stream has stopped
            s.amount = estimate_amount(s, cfg)
            out.append(s)
    return out


def estimate_amount(s: Series, cfg: RecurrenceConfig) -> Decimal:
    amounts = [m.amount for m in s.members[-cfg.lookback:]]
    if len(set(amounts)) == 1:
        return amounts[-1]
    if s.direction == "credit":
        # regular pay repeats exactly; a one-off dip is not the new normal
        common, n = Counter(amounts).most_common(1)[0]
        if n >= 2:
            return common
        return _estimate(amounts, cfg.credit_estimator)
    if cfg.discretionary_estimator and s.category in DISCRETIONARY:
        return _estimate(amounts, cfg.discretionary_estimator)
    return _estimate(amounts, cfg.debit_estimator)


def project(series: Series, start: date, end: date) -> list[date]:
    """Dates in [start, end] when the series is next expected (after its last member)."""
    dates: list[date] = []
    if series.next_date is not None:
        last = series.next_date
        if start <= last <= end:
            dates.append(last)
    elif series.members:
        last = series.last.on
    else:
        return dates
    k = 1
    while True:
        if series.monthly_dom:
            d = _add_months(last, k, last.day)
        else:
            d = last + timedelta(days=series.period_days * k)
        if d > end:
            break
        if d >= start:
            dates.append(d)
        k += 1
    return dates


def overdue(series: Series, start: date) -> list[date]:
    """Expected dates after the last member but before `start` that never appeared."""
    out = []
    if not series.members or series.next_date is not None:
        return out
    last = series.last.on
    k = 1
    while True:
        d = _add_months(last, k, last.day) if series.monthly_dom else last + timedelta(days=series.period_days * k)
        if d >= start:
            return out
        out.append(d)
        k += 1
