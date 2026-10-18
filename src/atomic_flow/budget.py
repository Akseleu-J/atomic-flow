"""Local spend ledger + run-artifact retention (the v0.1 `budget.daily_limit_usd` was never enforced)."""

from __future__ import annotations

import json
import math
import os
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from atomic_flow.config import user_dir
from atomic_flow.errors import BudgetExceeded
from atomic_flow.locking import exclusive_file


class SpendLedger:
    _lock = threading.Lock()

    def __init__(self, daily_limit_usd: float, directory: Path | None = None):
        self.limit = daily_limit_usd

        self.dir = directory or user_dir() / "ledger"

    @property
    def path(self) -> Path:
        return self.dir / f"{datetime.now(UTC).date().isoformat()}.jsonl"

    def today_total(self, *, strict: bool = False) -> float:
        entries = {}
        try:
            with self.path.open(encoding="utf-8") as handle:
                for i, line in enumerate(handle):
                    try:
                        row = json.loads(line)
                        cost = float(row.get("cost_usd") if row.get("cost_usd") is not None
                                     else row.get("reserved_usd") or 0.0)
                        if not math.isfinite(cost) or cost < 0:
                            raise ValueError("invalid ledger cost")
                        entries[row.get("call_id") or f"legacy_{i}"] = cost
                    except (json.JSONDecodeError, TypeError, ValueError, AttributeError) as exc:
                        if strict:
                            raise BudgetExceeded("invalid spend ledger; review it before more model calls") from exc
        except FileNotFoundError:
            pass
        except OSError:
            if strict:
                raise
        return sum(entries.values())

    def check(self, spent_this_request: float = 0.0) -> None:
        spent = self.today_total() + spent_this_request
        if self.limit > 0 and spent >= self.limit:
            raise BudgetExceeded(f"Daily limit ${self.limit:.2f} reached (${spent:.4f} spent/reserved today). "
                                 "Raise budget.daily_limit_usd or set it to 0 to disable.")

    def _append(self, fields):
        row = json.dumps({"ts": datetime.now(UTC).isoformat(), **fields}, ensure_ascii=False) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(row)
            handle.flush()
            os.fsync(handle.fileno())

    def reserve(self, **fields):
        """Atomic admission across cooperating processes; holds persist after a crash."""
        amount = fields["reserved_usd"]
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._lock, exclusive_file(self.dir / "spend.lock", blocking=True):
            total = self.today_total(strict=True)
            if self.limit > 0 and (total >= self.limit or total + amount > self.limit):
                raise BudgetExceeded("daily budget cannot cover the next model-call reservation")
            self._append({**fields, "status": "in_flight", "cost_usd": None})

    def record(self, **fields: object) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self._lock, exclusive_file(self.dir / "spend.lock", blocking=True):
                self._append(fields)
        except (OSError, ValueError):
            # Task calls already have a durable reservation and task-local response record.
            # The unresolved ledger reservation stays conservative if settlement fails.
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
