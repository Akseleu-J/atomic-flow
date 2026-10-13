"""Local spend ledger + run-artifact retention (the v0.1 `budget.daily_limit_usd` was never enforced)."""

from __future__ import annotations

import json
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from atomic_flow.config import user_dir
from atomic_flow.errors import BudgetExceeded


class SpendLedger:
    _lock = threading.Lock()

    def __init__(self, daily_limit_usd: float, directory: Path | None = None):
        self.limit = daily_limit_usd

        self.dir = directory or user_dir() / "ledger"

    @property
    def path(self) -> Path:
        return self.dir / f"{datetime.now(UTC).date().isoformat()}.jsonl"

    def today_total(self) -> float:
        total = 0.0
        try:
            with self.path.open(encoding="utf-8") as handle:
                lines = handle
                for line in lines:
                    try:
                        total += float(json.loads(line).get("cost_usd") or 0.0)
                    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
                        continue
        except OSError:
            pass
        return total

    def check(self, spent_this_request: float = 0.0) -> None:
        spent = self.today_total() + spent_this_request
        if self.limit > 0 and spent >= self.limit:
            raise BudgetExceeded(f"Daily limit ${self.limit:.2f} reached (${spent:.4f} spent today). "
                                 "Raise budget.daily_limit_usd or set it to 0 to disable. Only calls with a known cost are counted.")

    def record(self, **fields: object) -> None:
        row = json.dumps({"ts": datetime.now(UTC).isoformat(), **fields}, ensure_ascii=False) + "\n"
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(row)
        except OSError:
            pass


def prune_runs(keep_days: int, root: Path | None = None) -> int:
    """Delete run directories older than ``keep_days``. Artifacts contain source snippets and prompts."""
    root = root or user_dir() / "runs"
    if not root.is_dir():
        return 0
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for path in root.iterdir():
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path)
                removed += 1
        except OSError:
            continue
    return removed
