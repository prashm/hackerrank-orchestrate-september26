"""Turn raw financial events into a clean cash picture for one request.

Output:
  - history:   settled cash events before request_date (home currency), used to
               learn recurring patterns. The balance already reflects them.
  - committed: known future cash flows (pending debits, scheduled debits,
               confirmed scheduled credits) that the forecast must apply.
  - excluded:  everything ignored, with the reason (for traces).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from .fx import FxTable
from .schemas import Event, Profile

CREDIT_TYPES_NEVER_PROJECTED = {"refund", "investment_sale"}


@dataclass(frozen=True)
class CashEvent:
    event_id: str
    on: date                 # settlement date (cash date)
    amount: Decimal          # positive, home currency
    direction: str           # 'debit' | 'credit'
    event_type: str
    category: str
    description: str
    flexibility: str
    minimum_allowed_amount: Decimal | None
    status: str

    @property
    def signed(self) -> Decimal:
        return -self.amount if self.direction == "debit" else self.amount


@dataclass(frozen=True)
class EventOverride:
    """A fact from evidence (image/message) that changes one event."""
    amount: Decimal | None = None          # fills/amends the amount (event currency)
    cancelled: bool = False                 # cancellation / not going to happen
    settles_on: date | None = None          # delayed or moved settlement
    confirmed: bool | None = None           # confirms a pending/scheduled credit
    is_duplicate: bool | None = None        # explicit duplicate / not duplicate
    source: str = ""


@dataclass
class Ledger:
    history: list[CashEvent] = field(default_factory=list)
    committed: list[CashEvent] = field(default_factory=list)
    excluded: list[tuple[str, str]] = field(default_factory=list)  # (event_id, reason)
    unresolved_amounts: list[str] = field(default_factory=list)


def _to_cash(e: Event, amount: Decimal, home: str, fx: FxTable, on: date) -> CashEvent:
    home_amt = fx.convert(amount, e.currency, home, e.settlement_date or e.event_date)
    return CashEvent(
        event_id=e.event_id, on=on, amount=home_amt, direction=e.direction,
        event_type=e.event_type, category=e.category, description=e.description,
        flexibility=e.flexibility, minimum_allowed_amount=e.minimum_allowed_amount,
        status=e.status,
    )


def build_ledger(
    events: list[Event],
    profile: Profile,
    request_date: date,
    fx: FxTable,
    overrides: dict[str, EventOverride] | None = None,
    exclude: set[str] | None = None,
) -> Ledger:
    overrides = overrides or {}
    exclude = exclude or set()
    led = Ledger()
    by_id = {e.event_id: e for e in events}

    # Rows that a later row in the same lifecycle supersedes.
    superseded: set[str] = set()
    for e in events:
        if e.linked_event_id and e.status in ("settled", "scheduled") and e.event_type not in ("refund", "investment_valuation", "investment_sale"):
            parent = by_id.get(e.linked_event_id)
            if parent and parent.status in ("cancelled", "failed", "pending"):
                superseded.add(parent.event_id)

    for e in sorted(events, key=lambda x: (x.settlement_date or x.event_date, x.num)):
        ov = overrides.get(e.event_id, EventOverride())
        on = ov.settles_on or e.settlement_date or e.event_date

        if e.status in ("failed", "cancelled") or ov.cancelled:
            led.excluded.append((e.event_id, f"{'cancelled by evidence' if ov.cancelled else e.status}"))
            continue
        if e.direction == "non_cash" or e.status == "unrealized":
            led.excluded.append((e.event_id, "non-cash / unrealized"))
            continue
        if e.event_id in exclude:
            led.excluded.append((e.event_id, "excluded by evidence (transfer / reimbursement / one-off)"))
            continue
        if e.event_id in superseded:
            led.excluded.append((e.event_id, "superseded by linked event"))
            continue

        amount = ov.amount if ov.amount is not None else e.amount
        if amount is None:
            led.unresolved_amounts.append(e.event_id)
            led.excluded.append((e.event_id, "blank amount, no evidence"))
            continue

        if e.status == "settled":
            if ov.is_duplicate:
                led.excluded.append((e.event_id, f"duplicate ({ov.source})"))
                continue
            led.history.append(_to_cash(e, amount, profile.home_currency, fx, on))
            continue

        # pending / scheduled: future cash
        on = max(on, request_date)
        is_dup = ov.is_duplicate
        if is_dup is None and e.linked_event_id and "duplicate" in e.description.lower():
            is_dup = True
        if is_dup:
            led.excluded.append((e.event_id, "duplicate of linked settled charge"))
            continue
        if e.direction == "debit":
            led.committed.append(_to_cash(e, amount, profile.home_currency, fx, on))
            continue
        # credits: only confirmed salary-like scheduled income counts
        confirmed = ov.confirmed
        if confirmed is None:
            confirmed = e.status == "scheduled" and e.event_type == "income"
        if confirmed and e.event_type not in CREDIT_TYPES_NEVER_PROJECTED:
            led.committed.append(_to_cash(e, amount, profile.home_currency, fx, on))
        else:
            led.excluded.append((e.event_id, f"{e.status} credit not counted until settled"))

    return led


def with_amount(ev: CashEvent, amount: Decimal) -> CashEvent:
    return replace(ev, amount=amount)
