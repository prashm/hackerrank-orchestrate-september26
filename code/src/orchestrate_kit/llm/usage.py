"""Per-call token and cost accounting, rendered as a markdown usage report."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# USD per million tokens (input, output, cache read, cache write).
# Anthropic first-party list prices, cached 2026-10-06.
PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-fable-5-1": (10.00, 50.00, 0.25, 12.50),
}


@dataclass
class CallRecord:
    provider: str
    model: str
    purpose: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cached: bool = False          # served from the local result cache (no API call)

    @property
    def cost(self) -> float:
        if self.cached:
            return 0.0
        pin, pout, pcr, pcw = PRICING.get(self.model, (0, 0, 0, 0))
        return (self.input_tokens * pin + self.output_tokens * pout
                + self.cache_read_tokens * pcr + self.cache_write_tokens * pcw) / 1e6


@dataclass
class UsageLedger:
    records: list[CallRecord] = field(default_factory=list)

    def add(self, rec: CallRecord) -> None:
        self.records.append(rec)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps([asdict(r) for r in self.records], indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "UsageLedger":
        p = Path(path)
        if not p.exists():
            return cls()
        return cls([CallRecord(**r) for r in json.loads(p.read_text(encoding="utf-8"))])

    def render_markdown(self, *, title: str, n_requests: int, notes: list[str] | None = None) -> str:
        live = [r for r in self.records if not r.cached]
        by_model: dict[tuple[str, str], list[CallRecord]] = {}
        for r in self.records:
            by_model.setdefault((r.provider, r.model), []).append(r)
        lines = [f"# {title}", ""]
        lines += [f"- Requests in the run: **{n_requests}**",
                  f"- Model calls recorded: **{len(self.records)}** "
                  f"({len(live)} live API calls, {len(self.records) - len(live)} served from local cache)", ""]
        lines += ["## Per model", "",
                  "| Provider | Model | Calls (live/cached) | Input tokens | Output tokens | Cache read | Cache write | Est. cost (USD) |",
                  "|---|---|---|---|---|---|---|---|"]
        tot_in = tot_out = tot_cr = tot_cw = 0
        tot_cost = 0.0
        for (prov, model), recs in sorted(by_model.items()):
            i = sum(r.input_tokens for r in recs)
            o = sum(r.output_tokens for r in recs)
            cr = sum(r.cache_read_tokens for r in recs)
            cw = sum(r.cache_write_tokens for r in recs)
            c = sum(CallRecord(**{**asdict(r), "cached": False}).cost for r in recs)
            nl = sum(not r.cached for r in recs)
            lines.append(f"| {prov} | `{model}` | {nl}/{len(recs) - nl} | {i:,} | {o:,} | {cr:,} | {cw:,} | ${c:.4f} |")
            tot_in, tot_out, tot_cr, tot_cw, tot_cost = tot_in + i, tot_out + o, tot_cr + cr, tot_cw + cw, tot_cost + c
        total_tokens = tot_in + tot_out + tot_cr + tot_cw
        lines += ["", "## Overall", "",
                  "| Metric | Value |", "|---|---|",
                  f"| Input tokens | {tot_in:,} |",
                  f"| Output tokens | {tot_out:,} |",
                  f"| Cache read / write tokens | {tot_cr:,} / {tot_cw:,} |",
                  f"| Total tokens | {total_tokens:,} |",
                  f"| Average tokens per request | {total_tokens / max(n_requests, 1):,.1f} |",
                  f"| Estimated total cost (USD) | ${tot_cost:.4f} |",
                  f"| Estimated cost per request (USD) | ${tot_cost / max(n_requests, 1):.5f} |", ""]
        lines += ["## By purpose", "", "| Purpose | Calls | Input | Output |", "|---|---|---|---|"]
        purposes: dict[str, list[CallRecord]] = {}
        for r in self.records:
            purposes.setdefault(r.purpose, []).append(r)
        for p, recs in sorted(purposes.items()):
            lines.append(f"| {p} | {len(recs)} | {sum(r.input_tokens for r in recs):,} | {sum(r.output_tokens for r in recs):,} |")
        if notes:
            lines += ["", "## Notes", ""] + [f"- {n}" for n in notes]
        return "\n".join(lines) + "\n"
