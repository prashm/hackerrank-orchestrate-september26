"""90-day daily balance forecast and safety queries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable

from .ledger import CashEvent, Ledger
from .recurrence import RecurrenceConfig, Series, detect_series, overdue, project


@dataclass(frozen=True)
class ForecastConfig:
    horizon_days: int = 90
    include_end: bool = True          # window is [request_date, request_date + horizon]
    intraday: bool = True             # debits land before same-day credits (calibrated on samples)
    project_income: bool = True       # project recurring income series
    variable_income: bool = True      # also project income whose amount varies (gig, freelance) at its recent minimum
    project_on_start: bool = True     # project recurring items falling on request_date itself
    overdue_policy: str = "skip"      # skip | today  (series occurrences missed before request_date)
    recurrence: RecurrenceConfig = field(default_factory=RecurrenceConfig)


@dataclass(frozen=True)
class Flow:
    on: date
    amount: Decimal       # signed: negative = debit
    source: str           # event id or series label
    series_key: tuple | None = None
    kind: str = "projected"  # projected | committed


@dataclass
class Forecast:
    start: date
    end: date
    opening: Decimal
    minimum: Decimal
    flows: list[Flow]
    series: list[Series]
    low: list[Decimal] = field(default_factory=list)      # lowest running balance on each day
    close: list[Decimal] = field(default_factory=list)    # end-of-day balance
    intraday: bool = False

    def __post_init__(self) -> None:
        self.recompute()

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def idx(self, d: date) -> int:
        return (d - self.start).days

    def recompute(self) -> None:
        by_day: list[list[Decimal]] = [[] for _ in range(self.days)]
        for f in self.flows:
            i = self.idx(f.on)
            if 0 <= i < self.days:
                by_day[i].append(f.amount)
        bal = self.opening
        self.low, self.close = [], []
        for amts in by_day:
            lo = bal
            for a in sorted(amts):
                bal += a
                lo = min(lo, bal)
            self.low.append(lo if self.intraday else bal)
            self.close.append(bal)

    # --- queries -----------------------------------------------------------
    # A payment on day d lands after that day's flows (e.g. after a salary
    # credited the same day) and reduces every later balance.
    def _suffix_low(self) -> list[Decimal]:
        out = [Decimal(0)] * self.days
        m = Decimal("Infinity")
        for i in range(self.days - 1, -1, -1):
            out[i] = min(self.close[i], m)
            m = min(m, self.low[i])
        return out

    def headroom_from(self, d: date) -> Decimal:
        """Largest single payment on day d keeping every later balance >= minimum."""
        i = max(self.idx(d), 0)
        if i >= self.days:
            return Decimal(0)
        return self._suffix_low()[i] - self.minimum

    def is_safe(self, payments: list[tuple[date, Decimal]]) -> bool:
        return self.shortfall(payments) <= 0

    def shortfall(self, payments: list[tuple[date, Decimal]]) -> Decimal:
        """How far below the minimum the plan goes (<=0 means safe)."""
        cum = [Decimal(0)] * self.days
        for d, amt in payments:
            i = max(self.idx(d), 0)
            if i < self.days:
                cum[i] += amt
        worst = Decimal("-Infinity")
        before = Decimal(0)
        for i in range(self.days):
            through = before + cum[i]
            worst = max(worst, self.minimum - (self.low[i] - before), self.minimum - (self.close[i] - through))
            before = through
        return worst

    def earliest_full(self, amount: Decimal) -> date | None:
        """First day in the window when paying `amount` once is safe."""
        suffix = self._suffix_low()
        for i in range(self.days):
            if suffix[i] - self.minimum >= amount:
                return self.start + timedelta(days=i)
        return None

    def lowest_point(self) -> tuple[date, Decimal]:
        i = min(range(self.days), key=lambda k: self.low[k])
        return self.start + timedelta(days=i), self.low[i]

    def with_flows(self, flows: list[Flow]) -> "Forecast":
        return Forecast(self.start, self.end, self.opening, self.minimum, flows, self.series, intraday=self.intraday)


def _seed_confirmed_income(series: list[Series], ledger: Ledger) -> None:
    """A confirmed scheduled salary is the newest fact about pay: it continues
    monthly from its own date at its own amount (new job, raise, moved payday)."""
    for c in sorted(ledger.committed, key=lambda c: c.on):
        if c.direction != "credit" or c.event_type != "income":
            continue
        key = (c.event_type, c.category, c.direction)
        same = [x for x in series if x.key == key]
        # the series whose cadence lands closest to the confirmed date
        s = min(same, key=lambda x: min((abs((d - c.on).days) for d in project(x, c.on - timedelta(days=20), c.on + timedelta(days=20))), default=99), default=None)
        if s is None:
            s = Series(key, [], monthly_dom=c.on.day)
            series.append(s)
        s.members = [m for m in s.members if m.on < c.on] + [c]
        if s.monthly_dom is None:
            s.period_days = s.period_days or 30
        else:
            s.monthly_dom = c.on.day
        s.amount = c.amount


def same_payment(a: Decimal, b: Decimal, tolerance: Decimal = Decimal("0.10")) -> bool:
    return b > 0 and abs(a - b) <= b * tolerance


Adjuster = Callable[[list[Series], Ledger, date, date], list[Flow]]


def build_forecast(ledger: Ledger, opening: Decimal, minimum: Decimal, start: date,
                   cfg: ForecastConfig = ForecastConfig(), adjust: Adjuster | None = None) -> Forecast:
    end = start + timedelta(days=cfg.horizon_days if cfg.include_end else cfg.horizon_days - 1)
    series = detect_series(ledger.history, start, cfg.recurrence)
    _seed_confirmed_income(series, ledger)
    flows: list[Flow] = []
    if adjust is not None:
        flows.extend(adjust(series, ledger, start, end))
    committed_keys = {(c.event_type, c.category, c.direction) for c in ledger.committed}
    for c in ledger.committed:
        flows.append(Flow(c.on, c.signed, c.event_id, (c.event_type, c.category, c.direction), "committed"))
    for s in series:
        if s.direction == "credit" and not cfg.project_income:
            continue
        if (s.direction == "credit" and not cfg.variable_income and s.members
                and not s.is_regular_amount and s.next_date is None and s.last.status == "settled"):
            s.notes.append("variable income not projected (not confirmed)")
            continue
        dates = project(s, start, end)
        if not cfg.project_on_start:
            dates = [d for d in dates if d > start]
        if cfg.overdue_policy == "today":
            dates = [start for _ in overdue(s, start)] + dates
        # a committed row that IS the next occurrence (same payment, similar amount) replaces it;
        # a different one-off charge in the same category (pending fuel, pharmacy, a scheduled
        # insurance top-up) is an extra outflow and must not erase the regular payment
        if s.key in committed_keys:
            for c in ledger.committed:
                if c is s.last:
                    continue
                if (c.event_type, c.category, c.direction) == s.key and dates and same_payment(c.amount, s.amount):
                    nearest = min(dates, key=lambda d: abs((d - c.on).days))
                    if abs((nearest - c.on).days) <= 10:
                        dates.remove(nearest)
        s.occurrences = dates
        sign = -1 if s.direction == "debit" else 1
        for i, d in enumerate(dates):
            flows.append(Flow(d, sign * s.amount_for(i, d), s.describe(), s.key))
    return Forecast(start, end, opening, minimum, flows, series, intraday=cfg.intraday)
