"""Content-addressed JSON cache for LLM results.

The key is a hash of everything that determines the answer (model, prompt
version, schema, inputs). Reruns hit the cache, cost zero tokens, and return
byte-identical results.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def content_key(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, (bytes, bytearray)):
            h.update(bytes(p))
        else:
            h.update(json.dumps(p, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:24]


class JsonCache:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str, key: str) -> Path:
        return self.dir / f"{name}.{key}.json"

    def get(self, name: str, key: str) -> dict | None:
        p = self._path(name, key)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    def put(self, name: str, key: str, value: dict) -> None:
        # drop stale entries for the same name (prompt or input changed)
        for old in self.dir.glob(f"{name}.*.json"):
            old.unlink()
        self._path(name, key).write_text(json.dumps(value, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
