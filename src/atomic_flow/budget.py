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

    def _append(self, fields, *, path=None):
        row = json.dumps({"ts": datetime.now(UTC).isoformat(), **fields}, ensure_ascii=False) + "\n"
        with (path or self.path).open("a", encoding="utf-8") as handle:
            handle.write(row)
            handle.flush()
            os.fsync(handle.fileno())

    def reserve(self, **fields):
        """Atomic admission across cooperating processes; holds persist after a crash."""
        if self.limit > 0 and fields.get("prices_known") is False:
            raise BudgetExceeded("configure model prices before calls under a daily budget")
        amount = fields["reserved_usd"]
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._lock, exclusive_file(self.dir / "spend.lock", blocking=True):
            total = self.today_total(strict=True)
            if self.limit > 0 and (total >= self.limit or total + amount > self.limit):
                raise BudgetExceeded("daily budget cannot cover the next model-call reservation")
            self._append({**fields, "status": "in_flight", "cost_usd": None})

    def record(self, **fields: object) -> None:
        """Record on the existing call day; previously unseen legacy calls use today.

        Identity corruption raises instead of silently recording another day's
        charge. Ordinary I/O failure still retains the existing conservative hold.
        """
        from atomic_flow.state import StateError

        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self._lock, exclusive_file(self.dir / "spend.lock", blocking=True):
                matches = self._call_rows(fields) if fields.get("call_id") else []
                path = matches[0][0] if matches else self.path
                if matches and matches[-1][1].get("status") == "reconciled":
                    if fields.get("cost_usd") != matches[-1][1].get("cost_usd"):
                        raise StateError("cannot overwrite a reconciled spend call")
                    return
                self._append(fields, path=path)
        except StateError:
            raise
        except (OSError, ValueError):
            # Task calls already have a durable reservation and task-local response record.
            # The unresolved ledger reservation stays conservative if settlement fails.
            pass

    def _call_rows(self, fields):
        """Under spend.lock, prove exact identity across retained ledger days.

        Inspect today first, then retained days; all days are checked to refuse
        duplicate identities rather than trusting an incomplete fast-path hit.
        This costs O(retained ledger bytes); a transactional index is future work.
        """
        from atomic_flow.state import StateError

        paths = sorted(self.dir.glob("????-??-??.jsonl"), key=lambda item: item != self.path)
        matches = []
        for path in paths:
            try:
                with path.open(encoding="utf-8") as handle:
                    for line in handle:
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            raise StateError("invalid original spend ledger row")
                        if row.get("call_id") == fields["call_id"]:
                            if row.get("task_id") != fields.get("task_id"):
                                raise StateError("spend call identity belongs to a different task")
                            matches.append((path, row))
            except (ValueError, OSError) as exc:
                if isinstance(exc, StateError):
                    raise
                raise StateError("cannot validate original spend ledger") from exc
        if len({path for path, _ in matches}) > 1:
            raise StateError("spend call spans multiple ledger days; inspect it before settlement")
        return matches

    def settle(self, **fields: object) -> None:
        """Settle the original call/day exactly once, refusing identity conflicts."""
        from atomic_flow.state import StateError

        self.dir.mkdir(parents=True, exist_ok=True)
        with self._lock, exclusive_file(self.dir / "spend.lock", blocking=True):
            matches = self._call_rows(fields)
            if not matches:
                raise StateError("reconciliation requires the original spend reservation")
            if not any(row.get("status") == "in_flight" and type(row.get("reserved_usd")) in (int, float)
                       and math.isfinite(row["reserved_usd"]) and row["reserved_usd"] >= 0
                       for _, row in matches):
                raise StateError("reconciliation requires the original spend reservation")
            path = matches[0][0]
            for _, row in matches:
                if row.get("status") == "reconciled":
                    if any(row.get(key) != fields.get(key) for key in ("cost_usd", "reconciliation_id")):
                        raise StateError("spend call already reconciled with different evidence or cost")
                    return
            self._append(fields, path=path)


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
