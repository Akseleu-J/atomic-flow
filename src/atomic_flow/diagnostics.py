"""Read-only readiness checks; never starts Docker, a subprocess or a model."""
from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from atomic_flow.config import AtomicConfig, user_dir


def offline_diagnostics(project: Path, config: AtomicConfig) -> dict:
    root = project.expanduser().resolve()
    state = user_dir().resolve()
    existing = state
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    checks = []
    disk = shutil.disk_usage(existing)
    checks.append({"check": "disk", "status": "ok" if disk.free >= 256 * 1024 * 1024 else "warning",
                   "free_bytes": disk.free, "minimum_warning_bytes": 256 * 1024 * 1024})
    checks.append({"check": "state_permissions", "status": "ok" if os.access(existing, os.W_OK) else "error",
                   "path": str(existing), "method": "os.access; no write probe"})
    engine = shutil.which("docker")
    checks.append({"check": "execution", "status": "warning" if config.execution.mode == "local" else "ok" if engine else "unavailable",
                   "mode": config.execution.mode, "docker_cli_found": bool(engine),
                   "daemon_and_image_verified": False, "local_isolation": False})
    holds = []
    corrupt = []
    ledger = state / "ledger" / f"{datetime.now(UTC).date().isoformat()}.jsonl"
    try:
        if ledger.exists():
            if ledger.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("ledger_too_large")
            latest = {}
            for number, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("ledger_record_not_object")
                latest[record.get("call_id") or f"legacy_{number}"] = record
            holds = [{"call_id": key, "status": row.get("status", "unknown")}
                     for key, row in latest.items() if row.get("status") in {"in_flight", "unknown"}]
    except (OSError, UnicodeError, ValueError, TypeError):
        corrupt.append("daily_ledger")
    checks.append({"check": "spend_holds", "status": "error" if corrupt else "warning" if holds else "ok",
                   "unresolved": holds[:100], "truncated": len(holds) > 100})
    tasks = state / "tasks"
    inspected = 0
    if tasks.is_dir():
        from atomic_flow.agents.task import load
        for path in sorted(tasks.glob("*.json"))[:100]:
            inspected += 1
            try:
                load(path.stem)
            except (OSError, ValueError):
                corrupt.append(path.name)
    checks.append({"check": "durable_state", "status": "error" if corrupt else "ok",
                   "inspected_task_files": inspected, "scan_limit": 100, "corrupt": corrupt[:100]})
    return {"project": str(root), "offline": True, "model_calls": 0, "checks": checks,
            "healthy": all(check["status"] == "ok" for check in checks),
            "limits": "Docker daemon/image and remote services unprobed; directory access uses metadata only; bounded state scan."}
