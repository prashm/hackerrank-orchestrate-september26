"""Buy or Wait? entry point.

    python3 code/main.py              # full run -> <repo>/output.csv + code/evaluation/usage_report.md
    python3 code/main.py --samples    # score against dataset/sample_requests.csv
    python3 code/main.py --only request_03 --trace
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from buy_or_wait.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
