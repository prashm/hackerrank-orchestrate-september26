"""Human-readable rendering of a Score."""

from __future__ import annotations

from .scorer import Score


def render(score: Score, *, show_misses: bool = True, width: int = 70) -> str:
    lines = ["field                              acc      correct/total", "-" * 58]
    for r in score.fields.values():
        lines.append(f"{r.name:<34} {r.accuracy:6.1%}   {r.correct:>4}/{r.total}")
    lines.append("-" * 58)
    lines.append(f"{'rows fully correct':<34} {score.rows_all_correct / max(score.rows_total, 1):6.1%}   "
                 f"{score.rows_all_correct:>4}/{score.rows_total}")
    if show_misses:
        for r in score.fields.values():
            if not r.misses:
                continue
            lines.append(f"\n## misses: {r.name}")
            for rid, pred, gold in r.misses:
                lines.append(f"  {rid}\n    pred: {pred[:width]}\n    gold: {gold[:width]}")
    return "\n".join(lines)
