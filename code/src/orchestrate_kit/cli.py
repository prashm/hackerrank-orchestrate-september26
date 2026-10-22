"""Common command-line flags shared by challenge entry points."""

from __future__ import annotations

import argparse
from pathlib import Path


def find_repo_root(start: str | Path, marker: str = "AGENTS.md", default: str | Path | None = None) -> Path:
    """Walk up from `start` to the directory containing `marker`; else `default` if given."""
    p = Path(start).resolve()
    for d in [p, *p.parents]:
        if (d / marker).exists():
            return d
    if default is not None:
        return Path(default).resolve()
    raise FileNotFoundError(f"no {marker} above {start}")


def base_parser(description: str, *, default_out: str = "output.csv") -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--data", default=None, help="dataset directory (default: <repo>/dataset)")
    ap.add_argument("--out", default=default_out, help=f"output CSV path relative to repo root (default: {default_out})")
    ap.add_argument("--samples", action="store_true", help="run on solved samples and print a score report")
    ap.add_argument("--only", nargs="*", default=None, help="restrict to these record ids")
    ap.add_argument("--trace", action="store_true", help="print decision traces for processed records")
    ap.add_argument("--trace-dir", default=None, help="dump JSON traces into this directory")
    ap.add_argument("--no-llm", action="store_true", help="never call an LLM; use cached results only")
    ap.add_argument("--quiet", action="store_true")
    return ap
