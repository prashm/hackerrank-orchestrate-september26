"""A small rule runner for output contracts.

Rules are plain functions `(row, ctx) -> str | list[str] | None` returning
violation messages. Collect them in a RuleSet and run over all rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

Rule = Callable[[Mapping[str, Any], Any], "str | list[str] | None"]


@dataclass
class Violation:
    row_id: str
    rule: str
    message: str

    def __str__(self) -> str:
        return f"[{self.row_id}] {self.rule}: {self.message}"


@dataclass
class RuleSet:
    id_column: str
    rules: list[tuple[str, Rule]] = field(default_factory=list)

    def rule(self, name: str | None = None) -> Callable[[Rule], Rule]:
        def deco(fn: Rule) -> Rule:
            self.rules.append((name or fn.__name__, fn))
            return fn
        return deco

    def add(self, name: str, fn: Rule) -> None:
        self.rules.append((name, fn))

    def check_row(self, row: Mapping[str, Any], ctx: Any = None) -> list[Violation]:
        out: list[Violation] = []
        rid = str(row.get(self.id_column, "?"))
        for name, fn in self.rules:
            try:
                res = fn(row, ctx)
            except Exception as exc:  # a crashing rule is itself a violation
                res = f"rule raised {type(exc).__name__}: {exc}"
            if not res:
                continue
            for msg in [res] if isinstance(res, str) else res:
                out.append(Violation(rid, name, msg))
        return out

    def check(self, rows: Iterable[Mapping[str, Any]], ctx_for: Callable[[Mapping[str, Any]], Any] | None = None) -> list[Violation]:
        out: list[Violation] = []
        for r in rows:
            out.extend(self.check_row(r, ctx_for(r) if ctx_for else None))
        return out


# --- reusable rule builders -------------------------------------------------

def one_of(column: str, allowed: Iterable[str]) -> Rule:
    allowed = set(allowed)
    def rule(row, _ctx):
        if row.get(column) not in allowed:
            return f"{column}={row.get(column)!r} not in {sorted(allowed)}"
    return rule


def matches(column: str, pattern: str, *, allow_empty: bool = False) -> Rule:
    rx = re.compile(pattern)
    def rule(row, _ctx):
        v = row.get(column) or ""
        if allow_empty and v == "":
            return None
        if not rx.fullmatch(str(v)):
            return f"{column}={v!r} does not match /{pattern}/"
    return rule


def required(*columns: str) -> Rule:
    def rule(row, _ctx):
        missing = [c for c in columns if row.get(c) in (None, "")]
        if missing:
            return f"blank required columns {missing}"
    return rule
