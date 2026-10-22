"""Deterministic effects of extracted evidence on the ledger and forecast.

The LLM only labels facts; every financial consequence is decided here, so
the rules are reviewable, testable, and immune to instructions in the text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from orchestrate_kit.trace import Trace

from ..forecast import Flow
from ..fx import FxTable
from ..ledger import EventOverride, Ledger
from ..recurrence import Series
from ..schemas import Dataset, Event, Message, Profile
from .images import amount_of

INCOME_KEY = ("income", "salary", "credit")


@dataclass
class UserEvidence:
    overrides: dict[str, EventOverride] = field(default_factory=dict)
    facts: list[tuple[Message, dict]] = field(default_factory=list)
    exclude_history: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)


def _dec(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", "")) if s and s.strip() else None
    except InvalidOperation:
        return None


def _date(s: str) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _to_home(amount: Decimal, ccy: str, home: str, on: date, fx: FxTable) -> Decimal:
    if not ccy or ccy == home:
        return amount
    try:
        return fx.convert(amount, ccy, home, on)
    except LookupError:
        got = fx.latest_on_or_before(ccy, home, on)
        return amount * got[1] if got else amount


def gather(ds: Dataset, user_id: str, request_date: date,
           image_ext: dict[str, dict], msg_facts: dict[str, list[dict]]) -> UserEvidence:
    ev = UserEvidence()
    events = ds.events_by_user.get(user_id, [])
    by_id = {e.event_id: e for e in events}

    for ref in ds.images:
        if ref.user_id != user_id or not ref.related_event_id or ref.image_id not in image_ext:
            continue
        x = image_ext[ref.image_id]
        amt = amount_of(x)
        target = by_id.get(ref.related_event_id)
        if amt is None or target is None or target.amount is not None:
            continue
        ev.overrides[target.event_id] = EventOverride(amount=amt, source=ref.image_id)
        ev.notes.append(f"{target.event_id} amount {amt} {x.get('currency')} from {ref.image_id} ({x.get('amount_label')})")

    # duplicate salary documents: a blank-amount salary row whose image matches a
    # settled payroll credit in the same month is the same payment, not extra income
    for eid, ov in list(ev.overrides.items()):
        e = by_id[eid]
        if e.direction != "credit" or e.status != "settled":
            continue
        for other in events:
            if (other.event_id != eid and other.direction == "credit" and other.category == e.category
                    and other.amount == ov.amount and other.event_date.month == e.event_date.month
                    and abs((other.event_date - e.event_date).days) <= 31):
                ev.overrides[eid] = EventOverride(amount=ov.amount, is_duplicate=True, source=ov.source)
                ev.notes.append(f"{eid} duplicates {other.event_id} (same pay period, same net amount)")
                break

    msgs = [m for m in ds.messages_by_user.get(user_id, []) if m.sent_at.date() <= request_date]
    for m in sorted(msgs, key=lambda m: m.sent_at):
        for f in msg_facts.get(m.message_id, []):
            ev.facts.append((m, f))
            kind = f["kind"]
            rel = by_id.get(m.related_event_id) if m.related_event_id else None
            if kind == "charge_disputed_open" and rel is not None:
                ev.overrides[rel.event_id] = EventOverride(is_duplicate=False, source=m.message_id)
            elif kind in ("reimbursement_not_salary", "income_received_final") and rel is not None:
                ev.exclude_history.add(rel.event_id)
            elif kind == "internal_transfer":
                ev.exclude_history |= _transfer_pair(events, request_date)
    return ev


def _transfer_pair(events: list[Event], request_date: date) -> set[str]:
    """Most recent debit/credit pair with equal amounts within 3 days."""
    settled = sorted((e for e in events if e.status == "settled" and e.amount is not None),
                     key=lambda e: e.event_date, reverse=True)
    for c in settled:
        if c.direction != "credit":
            continue
        for d in settled:
            if d.direction == "debit" and d.amount == c.amount and abs((d.event_date - c.event_date).days) <= 3:
                return {c.event_id, d.event_id}
    return set()


def make_adjuster(evidence: UserEvidence, profile: Profile, fx: FxTable, trace: Trace):
    """Build the forecast hook that applies message facts to projected series."""
    home = profile.home_currency

    def adjust(series: list[Series], ledger: Ledger, start: date, end: date) -> list[Flow]:
        flows: list[Flow] = []
        income = [s for s in series if s.key == INCOME_KEY]
        primary = max(income, key=lambda s: (s.monthly_dom is not None, s.amount), default=None)

        def ensure_primary(on: date) -> Series:
            nonlocal primary
            if primary is None:
                primary = Series(INCOME_KEY, [], monthly_dom=on.day)
                series.append(primary)
            return primary

        for m, f in evidence.facts:
            kind, amt, d = f["kind"], _dec(f["amount"]), _date(f["date"])
            rel = m.related_event_id
            when = d or start
            home_amt = _to_home(amt, f["currency"], home, when, fx) if amt is not None else None
            label = f"{m.message_id}:{kind}"

            if kind == "salary_amount_change" and home_amt is not None and primary is not None:
                if f["scope"] == "next_payment_only":
                    primary.next_amount = home_amt
                    regular = _regular_amount(primary)
                    if regular is not None:
                        primary.amount = regular
                else:
                    primary.changes.append((d or start, home_amt))
                trace.step("fact", applied=label, amount=home_amt, date=d, scope=f["scope"])
            elif kind == "salary_date_change" and d is not None and d >= start:
                s = ensure_primary(d)
                s.next_date = d
                s.monthly_dom = d.day
                trace.step("fact", applied=label, next_salary=d)
            elif kind in ("salary_confirmed", "salary_resumes") and home_amt is not None:
                if d is not None and d >= start:
                    s = ensure_primary(d)
                    s.next_date, s.monthly_dom = d, d.day
                    s.amount = home_amt
                    s.changes = []
                    _drop_committed_income_near(ledger, d)
                elif primary is not None:
                    primary.amount = home_amt
                trace.step("fact", applied=label, amount=home_amt, date=d)
            elif kind == "base_salary_only":
                # only the base pay recurs; commission/variable series are not confirmed income
                # settled payroll history beats a stated figure (conflict rule: settled > estimate)
                keep = max((s for s in income if s.is_regular_amount), key=lambda s: s.amount, default=primary)
                for s in income:
                    if s is not keep and s in series:
                        series.remove(s)
                if keep is not None and home_amt is not None and d is None and not keep.is_regular_amount:
                    keep.amount = home_amt
                trace.step("fact", applied=label, base=home_amt, kept=keep.describe() if keep else None)
            elif kind == "income_not_confirmed" and rel is None and _is_payout(f):
                # platform payouts are not withdrawable until completed: variable payout streams are unconfirmed
                for s in income:
                    if not s.is_regular_amount and s in series:
                        series.remove(s)
                        trace.step("fact", applied=label, dropped=s.describe())
            elif kind == "income_ended":
                remaining = [x for _, x in evidence.facts if x["kind"] == "salary_confirmed"
                             and _date(x["date"]) is None and _dec(x["amount"])]
                if remaining or (amt is not None):
                    keep_amt = home_amt if home_amt is not None else _to_home(_dec(remaining[0]["amount"]), remaining[0]["currency"], home, start, fx)
                    best = min(income, key=lambda s: abs(s.amount - keep_amt), default=None)
                    for s in income:
                        if s is not best and s in series:
                            series.remove(s)
                    if best is not None:
                        best.amount = keep_amt
                else:
                    for s in income:
                        if s in series:
                            series.remove(s)
                    ledger.committed = [c for c in ledger.committed if c.direction != "credit"]
                trace.step("fact", applied=label, remaining=home_amt)
            elif kind == "one_time_income_confirmed" and home_amt is not None and d is not None:
                # an approved invoice is the confirmed part of variable freelance income; the
                # unapproved rest must not also be projected from history
                for s in income:
                    if not s.is_regular_amount and s in series:
                        series.remove(s)
                        trace.step("fact", applied=label, dropped=s.describe())
                if start <= d <= end:
                    flows.append(Flow(d, home_amt, label, None, "committed"))
                    trace.step("fact", applied=label, credit=home_amt, on=d)
            elif kind == "expense_change" and f["category"]:
                pct = _dec(f["percent"])
                for s in series:
                    if s.direction == "debit" and s.category == f["category"]:
                        new = home_amt if home_amt is not None else (s.amount * (1 + pct / 100) if pct is not None else None)
                        if new is not None:
                            s.changes.append((d or start, new.quantize(Decimal("0.01"))))
                            trace.step("fact", applied=label, series=s.describe(), new_amount=new)
            elif kind == "new_recurring_expense" and home_amt is not None:
                first = d or start
                s = Series(("expense", f["category"] or "other", "debit"), [], monthly_dom=first.day)
                s.next_date, s.amount = first, home_amt
                series.append(s)
                trace.step("fact", applied=label, new_series=f["category"], amount=home_amt, first=first)
            else:
                trace.step("fact", noted=label, summary=f["summary"])
        return flows

    return adjust


def _is_payout(f: dict) -> bool:
    text = (f.get("summary", "") + " " + f.get("category", "")).lower()
    return any(w in text for w in ("payout", "earnings", "gig", "platform"))


def _regular_amount(s: Series) -> Decimal | None:
    from collections import Counter
    amts = [m.amount for m in s.members]
    if not amts:
        return None
    amt, n = Counter(amts).most_common(1)[0]
    return amt if n >= 2 else None


def _drop_committed_income_near(ledger: Ledger, d: date) -> None:
    ledger.committed = [c for c in ledger.committed
                        if not (c.direction == "credit" and c.event_type == "income" and abs((c.on - d).days) <= 20)]
