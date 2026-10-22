"""Dated, direction-aware currency conversion from exchange_rates.csv."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from .schemas import Rate


class MissingRate(LookupError):
    pass


class FxTable:
    def __init__(self, rates: list[Rate]):
        self._rates = {(r.rate_date, r.from_currency, r.to_currency): r.rate for r in rates}

    def convert(self, amount: Decimal, from_ccy: str, to_ccy: str, on: date) -> Decimal:
        """Convert using the rate dated `on`; stated direction first, inverse as fallback."""
        if from_ccy == to_ccy:
            return amount
        r = self._rates.get((on, from_ccy, to_ccy))
        if r is not None:
            return amount * r
        inv = self._rates.get((on, to_ccy, from_ccy))
        if inv is not None:
            return amount / inv
        raise MissingRate(f"no {from_ccy}->{to_ccy} rate on {on}")

    def latest_on_or_before(self, from_ccy: str, to_ccy: str, on: date) -> tuple[date, Decimal] | None:
        best = None
        for (d, f, t), r in self._rates.items():
            if d > on:
                continue
            if (f, t) == (from_ccy, to_ccy):
                cand = (d, r)
            elif (f, t) == (to_ccy, from_ccy):
                cand = (d, Decimal(1) / r)
            else:
                continue
            if best is None or cand[0] > best[0]:
                best = cand
        return best
