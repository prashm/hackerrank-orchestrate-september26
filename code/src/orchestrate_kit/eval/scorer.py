"""Per-field comparison of predictions against solved samples."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Sequence

Comparator = Callable[[str, str], bool]


def exact(pred: str, gold: str) -> bool:
    return (pred or "").strip() == (gold or "").strip()


def numeric(rel_tol: float = 0.0, abs_tol: float = 0.005) -> Comparator:
    def cmp(pred: str, gold: str) -> bool:
        p, g = (pred or "").strip(), (gold or "").strip()
        if p == g:
            return True
        try:
            pd, gd = Decimal(p), Decimal(g)
        except InvalidOperation:
            return False
        return abs(pd - gd) <= max(Decimal(str(abs_tol)), Decimal(str(rel_tol)) * abs(gd))
    return cmp


def token_set(sep: str = "|", item_cmp: Comparator | None = None) -> Comparator:
    """Order-insensitive comparison of sep-joined lists."""
    def cmp(pred: str, gold: str) -> bool:
        ps = sorted(t for t in (pred or "").split(sep) if t)
        gs = sorted(t for t in (gold or "").split(sep) if t)
        if item_cmp is None:
            return ps == gs
        return len(ps) == len(gs) and all(item_cmp(a, b) for a, b in zip(ps, gs))
    return cmp


@dataclass
class FieldResult:
    name: str
    correct: int = 0
    total: int = 0
    misses: list[tuple[str, str, str]] = field(default_factory=list)  # (id, pred, gold)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


@dataclass
class Score:
    fields: dict[str, FieldResult]
    rows_all_correct: int
    rows_total: int


def score(
    preds: Sequence[Mapping[str, Any]],
    golds: Sequence[Mapping[str, Any]],
    comparators: Mapping[str, Comparator],
    id_column: str,
) -> Score:
    gold_by = {g[id_column]: g for g in golds}
    results = {name: FieldResult(name) for name in comparators}
    all_ok = 0
    for p in preds:
        g = gold_by.get(p[id_column])
        if g is None:
            continue
        row_ok = True
        for name, cmp in comparators.items():
            r = results[name]
            r.total += 1
            pv, gv = str(p.get(name, "") or ""), str(g.get(name, "") or "")
            if cmp(pv, gv):
                r.correct += 1
            else:
                row_ok = False
                r.misses.append((p[id_column], pv, gv))
        all_ok += row_ok
    return Score(results, all_ok, len([p for p in preds if p[id_column] in gold_by]))
