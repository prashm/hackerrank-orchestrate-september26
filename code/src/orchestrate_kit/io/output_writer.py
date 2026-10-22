"""Write prediction CSVs that satisfy an exact column contract."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


class OutputContractError(ValueError):
    pass


def write_output(
    path: str | Path,
    columns: Sequence[str],
    rows: Iterable[Mapping[str, Any]],
    *,
    expected_ids: Sequence[str] | None = None,
    id_column: str | None = None,
) -> int:
    """Write rows with exactly `columns`, in order. Returns the row count.

    If `expected_ids` is given, the written ids must match it one-to-one and
    the output keeps the order of `expected_ids`.
    """
    rows = list(rows)
    for r in rows:
        extra = set(r) - set(columns)
        if extra:
            raise OutputContractError(f"unexpected keys {sorted(extra)} in row {r}")
    if expected_ids is not None:
        id_column = id_column or columns[0]
        by_id = {}
        for r in rows:
            rid = r.get(id_column)
            if rid in by_id:
                raise OutputContractError(f"duplicate id {rid}")
            by_id[rid] = r
        missing = [i for i in expected_ids if i not in by_id]
        unknown = [i for i in by_id if i not in set(expected_ids)]
        if missing or unknown:
            raise OutputContractError(f"missing ids {missing[:5]}… unknown ids {unknown[:5]}…")
        rows = [by_id[i] for i in expected_ids]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(columns), lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: "" if r.get(c) is None else r.get(c) for c in columns})
    return len(rows)
