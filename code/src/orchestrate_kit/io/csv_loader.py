"""Typed CSV loading driven by dataclass annotations.

Declare a dataclass whose field names match the CSV header and whose
annotations say how to parse each cell:

    @dataclass(frozen=True)
    class Row:
        id: str
        amount: Decimal | None      # blank -> None
        when: date
        flags: PipeList             # "a|b" -> ("a", "b")
        ok: bool                    # "true"/"false"

    rows = load_rows("data.csv", Row)
"""

from __future__ import annotations

import csv
import dataclasses
import types
import typing
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

T = TypeVar("T")


class PipeList(tuple):
    """Marker type: a cell holding values separated by '|'. Blank -> ()."""


_TRUE = {"true", "1", "yes", "y", "t"}
_FALSE = {"false", "0", "no", "n", "f"}


class CsvSchemaError(ValueError):
    pass


def _parse_bool(raw: str) -> bool:
    v = raw.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ValueError(f"not a boolean: {raw!r}")


def _parse_date(raw: str) -> date:
    return date.fromisoformat(raw.strip()[:10])


def _parse_datetime(raw: str) -> datetime:
    return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))


def _parse_decimal(raw: str) -> Decimal:
    try:
        return Decimal(raw.strip().replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {raw!r}") from exc


_PARSERS: dict[Any, Callable[[str], Any]] = {
    str: lambda s: s,
    int: lambda s: int(s.strip()),
    float: lambda s: float(s.strip()),
    Decimal: _parse_decimal,
    bool: _parse_bool,
    date: _parse_date,
    datetime: _parse_datetime,
    PipeList: lambda s: PipeList(p.strip() for p in s.split("|") if p.strip()),
}


def _unwrap_optional(tp: Any) -> tuple[Any, bool]:
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(args) == 1 and len(typing.get_args(tp)) == 2:
            return args[0], True
    return tp, False


def _converter(tp: Any) -> Callable[[str], Any]:
    inner, optional = _unwrap_optional(tp)
    parse = _PARSERS.get(inner)
    if parse is None:
        raise CsvSchemaError(f"no parser registered for type {inner!r}")
    if inner is PipeList:
        return parse
    if optional:
        return lambda s: None if s.strip() == "" else parse(s)
    if inner is str:
        return parse
    def strict(s: str) -> Any:
        if s.strip() == "":
            raise ValueError("required value is blank")
        return parse(s)
    return strict


def register_parser(tp: Any, parse: Callable[[str], Any]) -> None:
    """Add a parser for a custom annotation type."""
    _PARSERS[tp] = parse


def read_dicts(path: str | Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def load_rows(path: str | Path, cls: type[T], *, strict_columns: bool = False) -> list[T]:
    """Parse every row of `path` into `cls`. Extra CSV columns are ignored unless strict."""
    if not dataclasses.is_dataclass(cls):
        raise CsvSchemaError(f"{cls!r} is not a dataclass")
    hints = typing.get_type_hints(cls)
    fields = [f.name for f in dataclasses.fields(cls)]
    convs = {name: _converter(hints[name]) for name in fields}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        missing = [f for f in fields if f not in header]
        if missing:
            raise CsvSchemaError(f"{path}: missing columns {missing}")
        if strict_columns and set(header) != set(fields):
            raise CsvSchemaError(f"{path}: unexpected columns {sorted(set(header) - set(fields))}")
        out: list[T] = []
        for lineno, raw in enumerate(reader, start=2):
            try:
                out.append(cls(**{n: convs[n](raw[n] or "") for n in fields}))
            except ValueError as exc:
                raise CsvSchemaError(f"{path}:{lineno}: {exc}") from exc
        return out


def index_by(rows: Iterable[T], key: str) -> dict[Any, T]:
    """Unique index: {row.key: row}. Raises on duplicate keys."""
    out: dict[Any, T] = {}
    for r in rows:
        k = getattr(r, key)
        if k in out:
            raise CsvSchemaError(f"duplicate {key}={k!r}")
        out[k] = r
    return out


def group_by(rows: Iterable[T], key: str) -> dict[Any, list[T]]:
    """Non-unique index preserving input order: {row.key: [rows...]}."""
    out: dict[Any, list[T]] = {}
    for r in rows:
        out.setdefault(getattr(r, key), []).append(r)
    return out
