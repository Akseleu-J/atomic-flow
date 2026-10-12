"""Local spend ledger + run-artifact retention (the v0.1 `budget.daily_limit_usd` was never enforced)."""

from __future__ import annotations

import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from atomic_flow.config import user_dir
from atomic_flow.errors import BudgetExceeded


class SpendLedger:
    def __init__(self, daily_limit_usd: float, path: Path | None = None):
        self.limit = daily_limit_usd
        self.path = path or user_dir() / "ledger.jsonl"
        self._base = self.today_total()

    def today_total(self) -> float:
        today = datetime.now(UTC).date().isoformat()
        total = 0.0
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(row.get("ts", "")).startswith(today):
                    total += float(row.get("cost_usd") or 0.0)
        except OSError:
            pass
        return total

    def check(self, spent_this_request: float = 0.0) -> None:
        if self.limit > 0 and self._base + spent_this_request >= self.limit:
            raise BudgetExceeded(f"Daily limit ${self.limit:.2f} reached (${self._base + spent_this_request:.4f} spent today). "
                                 "Raise budget.daily_limit_usd or set it to 0 to disable. Only calls with a known cost are counted.")

    def record(self, **fields: object) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            row = {"ts": datetime.now(UTC).isoformat(), **fields}
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
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
