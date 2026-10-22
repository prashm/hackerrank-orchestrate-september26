"""Per-request analysis: evidence -> ledger -> forecast -> capacity numbers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from orchestrate_kit.trace import Trace

from .evidence.apply import UserEvidence, gather, make_adjuster
from .forecast import Forecast, ForecastConfig, build_forecast
from .fx import FxTable
from .ledger import Ledger, build_ledger
from .schemas import Dataset, Profile, Request


@dataclass
class Analysis:
    request: Request
    profile: Profile
    ledger: Ledger
    forecast: Forecast
    evidence: UserEvidence
    safe_today: Decimal                  # amount_safe_to_pay (before spending changes)
    earliest_full: date | None
    trace: Trace = field(repr=False, default=None)


@dataclass
class Engine:
    ds: Dataset
    image_ext: dict[str, dict]
    msg_facts: dict[str, list[dict]]
    cfg: ForecastConfig = field(default_factory=ForecastConfig)

    def __post_init__(self) -> None:
        self.fx = FxTable(self.ds.rates)

    def analyze(self, req: Request, trace: Trace | None = None) -> Analysis:
        trace = trace or Trace(req.request_id, enabled=False)
        p = self.ds.profiles[req.user_id]
        ev = gather(self.ds, req.user_id, req.request_date, self.image_ext, self.msg_facts)
        for n in ev.notes:
            trace.step("evidence", note=n)
        led = build_ledger(self.ds.events_by_user.get(req.user_id, []), p, req.request_date, self.fx,
                           ev.overrides, ev.exclude_history)
        if led.unresolved_amounts:
            trace.flag("unresolved_blank_amount")
        fc = build_forecast(led, p.current_available_balance, p.minimum_balance_to_keep, req.request_date,
                            self.cfg, adjust=make_adjuster(ev, p, self.fx, trace))
        safe = max(Decimal(0), min(fc.headroom_from(req.request_date), req.requested_amount))
        ef = fc.earliest_full(req.requested_amount)
        trace.step("ledger", committed=[(c.event_id, c.on, c.signed, c.description) for c in led.committed],
                   excluded=led.excluded)
        trace.step("series", items=[s.describe() for s in fc.series])
        low_d, low_v = fc.lowest_point()
        trace.step("capacity", opening=p.current_available_balance, minimum=p.minimum_balance_to_keep,
                   lowest=(low_d, low_v), safe_today=safe, earliest_full=ef)
        return Analysis(req, p, led, fc, ev, safe, ef, trace)
