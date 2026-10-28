"""Host-owned tool outcomes, independent of model wording and provider protocol."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

Outcome = Literal["success", "error", "denied"]


@dataclass(frozen=True)
class ToolResult:
    outcome: Outcome
    payload: dict[str, Any]
    verification: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Any, *, outcome: Outcome | None = None) -> ToolResult:
        if not isinstance(payload, dict):
            payload = {"error": "tool returned a non-object result"}
        payload = dict(payload)
        status = payload.get("status")
        if status is not None and not isinstance(status, str):
            payload["error"] = "tool returned an invalid status"
            status = None
        if status in {"rolled_back", "rollback_failed"}:
            payload.setdefault("error", payload.get("reason") or status)
        if "command" in payload and type(payload.get("exit_code")) is not int:
            payload.setdefault("error", "command did not report an exit code")
        elif "exit_code" in payload and payload["exit_code"] != 0:
            payload.setdefault("error", f"command exit_code={payload['exit_code']}")
        resolved: Outcome = outcome or ("error" if payload.get("error") else "success")
        checks = payload.get("checks") or []
        if not isinstance(checks, list) or not all(isinstance(check, str) for check in checks):
            payload["error"] = "tool returned invalid verification checks"
            checks = []
            resolved = "error"
        verification = {}
        if status in {"applied", "rolled_back", "rollback_failed"}:
            verification = {check: ("passed" if check in checks else "not_run")
                            for check in (("syntax", "ordinary_test") if "ordinary_test" in checks else ("syntax", "pytest"))}
            if resolved != "success" and checks:
                verification[checks[-1]] = "failed"
        if "command" in payload:
            verification[str(payload["command"])] = "passed" if resolved == "success" else "failed"
        explicit = payload.get("verification")
        if isinstance(explicit, dict) and all(isinstance(key, str) and isinstance(value, str) and value in {"passed", "failed", "not_run"}
                                              for key, value in explicit.items()):
            verification = explicit
        return cls(resolved, payload, verification)

    def model_payload(self) -> dict[str, Any]:
        return {**self.payload, "outcome": self.outcome, "verification": self.verification}

    def summary(self) -> dict[str, Any]:
        """Keep proof and failure fields; do not copy source bodies into meta.json."""
        out = {key: self.payload[key] for key in (
            "status", "error", "exit_code", "command", "reason", "node_id", "path", "symbol", "checks", "edit_id",
            "content_hash", "index_revision", "changes", "paths", "result_id", "next_offset",
            "check_timed_out", "check_exit_code", "timed_out",
            "check_source_changed", "checked_source_revision",
            "read_complete", "source_complete", "instruction_sources",
            "provenance",
            "side_effects_may_have_occurred",
            "effect_not_started",
            "workspace_id", "root_id", "root_generation", "hash_algorithm", "version", "available", "data_only",
        ) if key in self.payload}
        for name in ("check_obligation_digest", "check_spec_digest", "check_receipt_digest"):
            value = self.payload.get(name)
            if type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value):
                out[name] = value
        for name in ("check_passed", "check_completed", "check_failed_completed"):
            if type(self.payload.get(name)) is bool:
                out[name] = self.payload[name]
        if type(self.payload.get("check_runner")) is str and self.payload["check_runner"] in {"pytest", "sympy-native"}:
            out["check_runner"] = self.payload["check_runner"]
        for name, minimum, maximum in (("check_timeout_seconds", 1, 180), ("check_result_exit_code", -(2**32), 2**32 - 1)):
            value = self.payload.get(name)
            if type(value) is int and minimum <= value <= maximum:
                out[name] = value
        bounds = {"memory_bytes": 1024**3, "cpus": 2, "pids": 128, "tmpfs_bytes": 128 * 1024**2,
                  "output_bytes_per_stream": 1_000_000}
        resources = self.payload.get("check_resources")
        if (type(resources) is dict and resources.keys() == bounds.keys()
                and all(type(resources[name]) is int and 1 <= resources[name] <= maximum for name, maximum in bounds.items())):
            out["check_resources"] = dict(resources)
        if isinstance(self.payload.get("counts"), dict):
            out["counts"] = self.payload["counts"]
        if isinstance(self.payload.get("results"), list):
            out["results_count"] = len(self.payload["results"])
        return {**out, "outcome": self.outcome, "verification": self.verification}
