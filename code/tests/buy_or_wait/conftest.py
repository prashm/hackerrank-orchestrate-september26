"""Builders for small in-memory scenarios: one user, a few months of history, one request."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

from orchestrate_kit.trace import Trace

from buy_or_wait.engine import Engine
from buy_or_wait.planner import decide
from buy_or_wait.schemas import (Dataset, Event, ImageRef, Message, PaymentOption, Profile, Rate,
                                 Request)

REQ_DATE = date(2025, 6, 3)


@dataclass
class Scenario:
    """Fluent builder. Amounts are strings or numbers; currency defaults to EUR."""
    balance: str = "5000"
    minimum: str = "1000"
    currency: str = "EUR"
    protect: tuple = ("rent", "utilities", "groceries")
    reduce: tuple = ("dining",)
    stop: tuple = ("streaming",)
    methods: tuple = ("full_payment", "partial_payment", "installments")
    max_months: int | None = 6
    events: list[Event] = field(default_factory=list)
    options: list[PaymentOption] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)
    rates: list[Rate] = field(default_factory=list)
    image_ext: dict = field(default_factory=dict)
    msg_facts: dict = field(default_factory=dict)
    _n: int = 0

    # --- events --------------------------------------------------------------
    def event(self, on, amount, *, direction="debit", category="rent", typ="expense", status="settled",
              settles=None, desc=None, ccy=None, linked=None, flex="fixed", min_allowed=None) -> str:
        self._n += 1
        eid = f"event_{self._n}"
        self.events.append(Event(
            eid, "user_1", typ, desc or f"{category} payment", category, direction,
            None if amount is None else D(str(amount)), ccy or self.currency, on, settles or on, status,
            linked, flex, None if min_allowed is None else D(str(min_allowed))))
        return eid

    def monthly(self, day, amount, months=4, **kw) -> list[str]:
        """`months` settled occurrences on `day`, ending in the month before REQ_DATE."""
        ids = []
        for k in range(months, 0, -1):
            m = REQ_DATE.month - k
            y = REQ_DATE.year + (m - 1) // 12
            m = (m - 1) % 12 + 1
            ids.append(self.event(date(y, m, day), amount, **kw))
        return ids

    def every(self, days, amount, last, count=8, **kw) -> list[str]:
        amounts = amount if isinstance(amount, list) else [amount] * count
        return [self.event(last - timedelta(days=days * (len(amounts) - 1 - i)), a, **kw)
                for i, a in enumerate(amounts)]

    def salary(self, amount="3000", day=15, months=4, **kw) -> list[str]:
        return self.monthly(day, amount, months, direction="credit", category="salary", typ="income", **kw)

    def option(self, method, amount, n, first, every=None, fee="0") -> str:
        oid = f"payment_option_{len(self.options) + 1:02d}"
        amt = D(str(amount))
        self.options.append(PaymentOption(oid, "request_1", method, amt, n, first, every, D(fee),
                                          amt * n))
        return oid

    def message(self, text="...", related=None, facts=(), source="employer", sent=None) -> str:
        mid = f"message_{len(self.messages) + 1:02d}"
        self.messages.append(Message(mid, "user_1", "request_1", related,
                                     sent or datetime(2025, 6, 1, 9, 30, tzinfo=timezone.utc), source, text))
        self.msg_facts[mid] = [fact(**f) for f in facts]
        return mid

    # --- run -----------------------------------------------------------------
    def request(self, amount, *, deadline=None, partial=True, when=REQ_DATE) -> Request:
        return Request("request_1", "user_1", when, "purchase", D(str(amount)),
                       deadline or when + timedelta(days=60), partial, "Can I afford it?")

    def dataset(self, req: Request) -> Dataset:
        prof = Profile("user_1", self.currency, D(self.balance), D(self.minimum), (), self.protect,
                       self.reduce, self.stop, self.methods, self.max_months)
        return Dataset(Path("."), {"user_1": prof}, {"user_1": self.events},
                       {e.event_id: e for e in self.events}, self.rates, [req], [],
                       {"request_1": self.options}, {"user_1": self.messages}, self.images)

    def analyze(self, amount, **kw):
        req = self.request(amount, **kw)
        return Engine(self.dataset(req), self.image_ext, self.msg_facts).analyze(req, Trace("t"))

    def decide(self, amount, **kw):
        a = self.analyze(amount, **kw)
        return decide(a, self.options, Trace("t"))


def fact(kind, amount="", currency="", date="", percent="", scope="", category="", summary="") -> dict:
    return {"kind": kind, "amount": str(amount), "currency": currency, "date": date, "percent": str(percent),
            "scope": scope, "category": category, "summary": summary}


@pytest.fixture
def sc() -> Scenario:
    return Scenario()
