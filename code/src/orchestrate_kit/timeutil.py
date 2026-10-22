"""Date helpers shared across challenges."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def time_remaining(deadline_iso: str, now: datetime | None = None) -> str:
    """'Xd Yh Zm' until the deadline, or 'passed Xd Yh Zm ago'."""
    deadline = datetime.fromisoformat(deadline_iso)
    now = now or datetime.now(timezone.utc)
    delta = deadline - now
    past = delta.total_seconds() < 0
    secs = int(abs(delta.total_seconds()))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    text = f"{d}d {h}h {m}m"
    return f"passed {text} ago" if past else text


def daterange(start: date, end: date):
    """Inclusive range of dates."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def human_date(d: date) -> str:
    """2025-08-08 -> '8 August 2025'."""
    return f"{d.day} {d.strftime('%B')} {d.year}"
