"""Build a submission zip of the code directory."""

from __future__ import annotations

import fnmatch
import zipfile
from pathlib import Path

DEFAULT_EXCLUDES = (
    ".env", ".env.*", "*.pyc", "__pycache__", "*.egg-info", ".pytest_cache",
    ".venv", "venv", ".DS_Store", "traces", "*.zip",
)


def _excluded(rel: Path, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(part, pat) for part in rel.parts for pat in patterns)


def build_zip(src_dir: str | Path, zip_path: str | Path, *, arc_root: str = "code",
              excludes: tuple[str, ...] = DEFAULT_EXCLUDES) -> list[str]:
    """Zip `src_dir` into `zip_path` under `arc_root/`. Returns archived names."""
    src_dir, zip_path = Path(src_dir).resolve(), Path(zip_path).resolve()
    names: list[str] = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src_dir.rglob("*")):
            rel = p.relative_to(src_dir)
            if p.is_dir() or p == zip_path or _excluded(rel, excludes):
                continue
            arc = f"{arc_root}/{rel.as_posix()}"
            zf.write(p, arc)
            names.append(arc)
    return names
