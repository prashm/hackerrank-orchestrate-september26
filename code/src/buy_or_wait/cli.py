"""Entry point: evidence -> analysis -> decision -> validated output.csv."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

from orchestrate_kit.cli import base_parser, find_repo_root
from orchestrate_kit.eval import report, scorer
from orchestrate_kit.io.output_writer import write_output
from orchestrate_kit.llm.cache import JsonCache
from orchestrate_kit.llm.client import AnthropicProvider, StructuredLLM
from orchestrate_kit.llm.usage import UsageLedger
from orchestrate_kit.trace import Trace

from .engine import Engine
from .evidence import images, messages
from .explain import explain
from .planner import Decision, decide
from .rules import rules
from .schemas import OUTPUT_COLUMNS, Request, load_dataset

CODE_DIR = Path(__file__).resolve().parents[2]


def fmt_plain(x: Decimal) -> str:
    x = x.quantize(Decimal("0.01"))
    s = f"{x:f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def to_row(d: Decision) -> dict:
    req = d.analysis.request
    return {
        "request_id": req.request_id,
        "amount_safe_to_pay": fmt_plain(d.safe_today),
        "affordability_status": d.status,
        "recommended_payment_method": d.method,
        "payment_plan": d.payment_plan,
        "earliest_date_for_full_payment": d.earliest_full.isoformat() if d.earliest_full else "",
        "spending_changes_needed": d.spending_changes,
        "decision_explanation": explain(d),
    }


COMPARATORS = {
    "amount_safe_to_pay": scorer.numeric(rel_tol=0.01),
    "affordability_status": scorer.exact,
    "recommended_payment_method": scorer.exact,
    "payment_plan": scorer.exact,
    "earliest_date_for_full_payment": scorer.exact,
    "spending_changes_needed": scorer.token_set(),
}


def main(argv: list[str] | None = None) -> int:
    ap = base_parser("Buy or Wait? financial decision agent")
    ap.add_argument("--usage-report", default="evaluation/usage_report.md",
                    help="usage report path relative to code/ (written on full runs)")
    ap.add_argument("--config", default=None, help="JSON overrides for ForecastConfig (calibration)")
    args = ap.parse_args(argv)
    root = find_repo_root(CODE_DIR, default=CODE_DIR.parent)  # unzipped standalone: code/ sits next to dataset/
    ds = load_dataset(Path(args.data) if args.data else root / "dataset")

    usage = UsageLedger()
    provider = None
    if not args.no_llm:
        try:
            provider = AnthropicProvider()
        except Exception as exc:  # SDK missing or no credentials: cached extractions still work
            print(f"note: LLM unavailable ({type(exc).__name__}); using cached evidence only", file=sys.stderr)
    offline = provider is None
    img = images.extract_images(ds, StructuredLLM(provider, JsonCache(CODE_DIR / "cache/images"), usage, offline=offline))
    msg = messages.extract_messages(ds, StructuredLLM(provider, JsonCache(CODE_DIR / "cache/messages"), usage, offline=offline))
    engine = Engine(ds, img, msg)
    if args.config:
        import json
        from .forecast import ForecastConfig
        from .recurrence import RecurrenceConfig
        kw = json.loads(args.config)
        engine.cfg = ForecastConfig(**{**kw, "recurrence": RecurrenceConfig(**kw.get("recurrence", {}))})

    targets: list[Request] = list(ds.samples if args.samples else ds.requests)
    if args.only:
        targets = [r for r in targets if r.request_id in set(args.only)]

    rows, violations = [], []
    for req in targets:
        tr = Trace(req.request_id, enabled=args.trace or bool(args.trace_dir))
        opts = ds.options_by_request.get(req.request_id, [])
        decision = decide(engine.analyze(req, tr), opts, tr)
        row = to_row(decision)
        rows.append(row)
        ctx = {"request": req, "options": opts, "profile": ds.profiles[req.user_id], "events": ds.events}
        violations += rules.check_row(row, ctx)
        if args.trace:
            print(tr.render())
            print("  ->", {k: row[k] for k in OUTPUT_COLUMNS[1:7]})
        if args.trace_dir:
            tr.dump(CODE_DIR / args.trace_dir)

    for v in violations:
        print(f"VIOLATION {v}", file=sys.stderr)

    if args.samples:
        golds = [{c: getattr(s, c) for c in OUTPUT_COLUMNS} for s in ds.samples]
        print(report.render(scorer.score(rows, golds, COMPARATORS, "request_id"), show_misses=not args.quiet))
        return 1 if violations else 0

    out = root / args.out
    n = write_output(out, OUTPUT_COLUMNS, rows,
                     expected_ids=None if args.only else [r.request_id for r in ds.requests])
    if not args.quiet:
        print(f"wrote {n} rows to {out} ({len(violations)} contract violations)")
    if not args.only:
        notes = [
            "LLM is used only for evidence extraction: 16 document images (vision) and all messages (batched). "
            "Every financial decision and explanation is computed deterministically in Python.",
            "Results are cached by content hash in code/cache/; reruns with unchanged inputs make zero API calls "
            "(cached calls are listed with their original token counts and $0 incremental cost).",
            "Prices: Anthropic list prices per million tokens; cost includes prompt-cache reads/writes.",
        ]
        report_path = CODE_DIR / args.usage_report
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(usage.render_markdown(title="Token usage: final full-dataset run",
                                                     n_requests=len(rows), notes=notes), encoding="utf-8")
        usage.save(CODE_DIR / "evaluation/usage_calls.json")
    return 1 if violations else 0
