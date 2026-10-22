"""Build <repo>/code.zip for submission (excludes .env, venv, caches of Python, traces)."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from orchestrate_kit.packaging import build_zip  # noqa: E402

if __name__ == "__main__":
    code = Path(__file__).resolve().parent
    names = build_zip(code, code.parent / "code.zip")
    assert "code/evaluation/usage_report.md" in names, "usage report missing - run a full pass first"
    print(f"code.zip: {len(names)} files")
