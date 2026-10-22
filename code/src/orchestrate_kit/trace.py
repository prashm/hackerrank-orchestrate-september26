"""Structured per-record decision traces.

A Trace collects named steps with data while a record is processed. It can be
pretty-printed for debugging or dumped to JSON for auditing.

    tr = Trace("request_03")
    tr.step("headroom", min_balance=..., on=...)
    tr.note("rejected installments: last payment after deadline")
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_jsonable(x) for x in v]
    if hasattr(v, "__dataclass_fields__"):
        return {k: _jsonable(getattr(v, k)) for k in v.__dataclass_fields__}
    return v


@dataclass
class Trace:
    record_id: str
    enabled: bool = True
    steps: list[dict[str, Any]] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)

    def step(self, name: str, **data: Any) -> None:
        if self.enabled:
            self.steps.append({"step": name, **data})

    def note(self, text: str) -> None:
        if self.enabled:
            self.steps.append({"step": "note", "text": text})

    def flag(self, name: str) -> None:
        """Mark the record (e.g. 'used_llm_fact', 'inferred_amount') for review."""
        self.flags.add(name)

    def to_dict(self) -> dict[str, Any]:
        return {"record_id": self.record_id, "flags": sorted(self.flags), "steps": _jsonable(self.steps)}

    def dump(self, directory: str | Path) -> Path:
        p = Path(directory) / f"{self.record_id}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return p

    def render(self, max_list: int = 200) -> str:
        lines = [f"=== trace {self.record_id}  flags={sorted(self.flags) or '-'}"]
        for s in self.steps:
            s = _jsonable(s)
            name = s.pop("step")
            if name == "note":
                lines.append(f"  · {s['text']}")
                continue
            lines.append(f"  [{name}]")
            for k, v in s.items():
                if isinstance(v, list) and v and isinstance(v[0], (dict, list)):
                    lines.append(f"    {k}:")
                    for item in v[:max_list]:
                        lines.append(f"      {item}")
                    if len(v) > max_list:
                        lines.append(f"      … {len(v) - max_list} more")
                else:
                    lines.append(f"    {k}: {v}")
        return "\n".join(lines)
