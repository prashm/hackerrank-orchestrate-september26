"""Score the solver on dataset/sample_requests.csv (per-field accuracy + misses).

    python3 code/evaluation/main.py [--no-llm] [--quiet]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from buy_or_wait.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["--samples", *sys.argv[1:]]))
