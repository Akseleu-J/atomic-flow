"""Real Docker public Atomic/flat safety journeys; scripted callbacks, zero provider calls.

This is synthetic execution/host-boundary qualification. Scripted action costs
are not measured model utility or a paid matched comparison.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.backends.base import ChatResult
from atomic_flow.config import BackendConfig, user_dir
from atomic_flow.sessions import SessionJournal

if __package__:
    from .p07_batch_obligation_journey import journey
    from .p07_flat_task_runner import FlatTaskRunner
else:
    from p07_batch_obligation_journey import journey
    from p07_flat_task_runner import FlatTaskRunner

EXPECTED_SOURCES = {"a.py": "from b import renamed\n\ndef call():\n    return renamed()\n",
                    "b.py": "def renamed():\n    return 42\n"}
SCOPE = "Synthetic public two-file TaskRunner/Docker safety integration; not paid quality, utility or hidden oracle success"


class FlatScriptedBackend:
    name = "scripted_public_flat"

    def __init__(self, originals):
        self.config = BackendConfig(model="scripted_public_flat", max_retries=0, temperature=None,
                                    max_input_tokens=200000, max_output_tokens=4096,
                                    price_in_per_mtok=0, price_out_per_mtok=0)
        self.calls = []
        self.answers = [
            {"tool": "run_command", "args": {"command": "check"}},
            {"tool": "read_file", "args": {"path": "a.py"}},
            {"tool": "read_file", "args": {"path": "b.py"}},
            {"tool": "patch_files", "args": {"patches": [
                {"path": path, "expected_hash": hashlib.sha1(source.encode()).hexdigest(),
                 "old_text": source, "new_text": source.replace("original", "renamed")}
                for path, source in originals.items()]}},
            {"tool": "run_command", "args": {"command": "check"}},
            {"done": True, "summary": "Coupled rename prepared; request independent host completion."},
        ]

    def chat(self, messages, *, json_mode=False, model=None):
        if model is not None and model != self.config.model:
            raise ValueError("scripted flat model substitution refused")
        if len(messages) < 2 or any("<role>" in message["content"] for message in messages) or not self.answers:
            raise ValueError("unexpected flat scaffold role or extra scripted callback")
        self.calls.append(("flat_turn", copy.deepcopy(messages)))
        return ChatResult(json.dumps(self.answers.pop(0)), tokens_in=0, tokens_out=0, cost_usd=0,
                          latency_ms=0, cost_source="scripted_zero_provider_calls")

    def roles(self):
        return [role for role, _ in self.calls]


def _write(path, value):
    path.write_bytes((json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))


def _export_sessions(output):
    records = []
    root = user_dir() / "sessions"
    if not root.exists():
        return records
    total = 0
    for path in sorted(root.rglob("*.jsonl")):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024**2:
            raise ValueError("unsafe host session evidence")
        raw = path.read_bytes()
        total += len(raw)
        if total > 64 * 1024**2:
            raise ValueError("host session evidence exceeded bound")
        relative = path.relative_to(root)
        target = output / "host-sessions" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        records.append({"path": target.relative_to(output).as_posix(), "sha256": hashlib.sha256(raw).hexdigest(),
                        "bytes": len(raw)})
    _write(output / "host-session-manifest.json", records)
    return records


def qualifies(report, arm):
    obligation = report.get("obligation_digest")
    if type(obligation) is not str or len(obligation) != 64 or set(obligation) - set("0123456789abcdef"):
        return False
    if (report.get("status") != "scripted_public_coupled_obligation_journey_qualified"
            or report.get("task_status") != "done" or report.get("replan_count") != 1
            or report.get("final_source") != EXPECTED_SOURCES
            or report.get("completion", {}).get("ready_to_publish") is not True
            or report.get("completion", {}).get("check_passed") is not True
            or report.get("completion", {}).get("check_completed") is not True
            or report.get("completion", {}).get("check_obligation_digest") != report.get("obligation_digest")
            or report.get("graph_sync", {}).get("failed") is not False
            or report.get("graph_sync", {}).get("coverage_complete") is not True):
        return False
    if arm == "flat":
        events = report.get("budget_events", [])
        binding = report.get("approved_runtime_digest")
        return (type(binding) is str and len(binding) == 64 and not set(binding) - set("0123456789abcdef")
                and report.get("execution_strategy") == "flat-baseline-v1"
                and report.get("backend_roles") == ["flat_turn"] * 6 and len(events) == 6
                and report.get("remaining_scripted_answers") == 0
                and report.get("budget_spent_usd") == 0
                and all(event.get("status") == "completed" and event.get("cost_usd") == 0
                        and event.get("cost_source") == "scripted_zero_provider_calls" for event in events))
    return arm == "atomic"


def matched_public_journey(output: Path, arm: str):
    if arm not in {"atomic", "flat"}:
        raise ValueError("fixed public qualification arm required")
    captured = {}

    def backend_factory(*, originals, step, output):
        backend = FlatScriptedBackend(originals)
        return backend

    def runner_factory(config, project, backend, **kwargs):
        selected = config.model_copy(deep=True)
        if arm == "flat":
            selected.task.execution_strategy = "flat-baseline-v1"
            actual = FlatTaskRunner(selected, project, backend, **kwargs)
        else:
            actual = TaskRunner(selected, project, backend, **kwargs)

        class Capture:
            def run_plan(self, *args, **options):
                try:
                    task = actual.run_plan(*args, **options)
                except BaseException:
                    captured["session_manifest"] = _export_sessions(output)
                    _write(output / "backend-requests.json", backend.calls)
                    raise
                captured["execution_strategy"] = actual.config.task.execution_strategy
                captured["approved_runtime_digest"] = task.plan_approval.get("internal_replan", {}).get("runtime_digest")
                captured["shared_limits"] = {
                    "max_model_calls": actual.config.task.max_model_calls,
                    "max_budget_usd": actual.config.task.max_budget_usd,
                    "max_replans": actual.config.task.max_replans,
                    "max_duration_seconds": actual.config.task.max_duration_seconds,
                    "ordinary_deadline_scope": "same fixed 600-second obligation from fixture construction",
                    "image": actual.config.execution.image,
                    "ordinary_test_paths": list(actual.check_obligation.template.test_paths),
                    "check_command": "check",
                    "backend_input_cap": backend.config.max_input_tokens,
                    "backend_output_cap": backend.config.max_output_tokens}
                captured["session_manifest"] = _export_sessions(output)
                journal = SessionJournal(task.id)
                if journal.path.exists():
                    _write(output / "task-session.json", journal.read().events)
                _write(output / "backend-requests.json", backend.calls)
                if arm == "flat":
                    captured["remaining_scripted_answers"] = len(backend.answers)
                return task

        return Capture()

    report = journey(output, "check", backend_factory=backend_factory if arm == "flat" else None,
                     runner_factory=runner_factory)
    report.update(captured)
    accepted = qualifies(report, arm)
    report.update(
        status="scripted_public_" + arm + "_task_journey_qualified" if accepted else "not_qualified",
        arm=arm, scope=SCOPE, provider_calls=0, model_generation=False,
        hidden_oracle="not_run", independent_requirement_success=False, measured_model_utility=False,
        scripted_cost_scope=("Explicit zero script usage" if arm == "flat" else
                             "Historical scripted fake usage values retained; not provider charges or utility measurements"),
        tested_commit=os.environ.get("GITHUB_SHA", "local_unpinned"))
    if output.is_dir():
        _write(output / "result.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arm", choices=("atomic", "flat"), required=True)
    args = parser.parse_args()
    report = matched_public_journey(args.output, args.arm)
    print(json.dumps({key: report.get(key) for key in
                      ("status", "arm", "tested_commit", "provider_calls", "model_generation", "measured_model_utility")}))
    return 0 if report["status"] == "scripted_public_" + args.arm + "_task_journey_qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
