"""Synthetic full-source native SymPy TaskRunner journey, with zero provider calls.

Uses public built-in tools with ordinary_test or legacy pytest verification.
Actual observations must name sympy-native; graph coverage omissions are explicit.
This is neither a paid campaign nor hidden-oracle or whole-30 quality evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

from p07_sympy_check_smoke import IMAGE, INTERPRETER, MODULE_PATH, ORDINARY_TEST, ROOT, TEST_PATH, save, sha, smoke

from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.agents.verify import check_source_revision, check_source_snapshot
from atomic_flow.check_obligation import CheckObligation
from atomic_flow.check_spec import CheckSpec
from atomic_flow.config import AtomicConfig, trust_project
from atomic_flow.tree.builder import build_tree, save_tree

QUALIFIED = "scripted_full_source_native_sympy_task_journey_qualified"


def _observations(state, task, output):
    """Export bounded host summaries, never prompts or raw target source."""
    observations = []
    for step in task.steps:
        if not step.request_id:
            continue
        path = state / "runs" / step.request_id / "meta.json"
        if not path.is_file() or path.stat().st_size > 16 * 1024**2:
            raise ValueError("Missing or oversized public request artifact")
        metadata = json.loads(path.read_text(encoding="utf-8"))
        for call in metadata.get("tool_calls", []):
            summary = call.get("result_summary", {})
            if not isinstance(summary, dict):
                raise ValueError("Invalid host tool summary")
            allowed = {"checked_source_revision", "command", "exit_code", "status", "outcome", "verification",
                       "check_obligation_digest", "check_spec_digest", "check_receipt_digest", "check_passed",
                       "check_completed", "check_failed_completed", "check_runner", "check_timeout_seconds",
                       "check_resources", "check_result_exit_code", "check_timed_out", "check_exit_code", "check_source_changed"}
            fields = {key: value for key, value in summary.items() if key in allowed}
            encoded = json.dumps(fields, sort_keys=True, allow_nan=False).encode()
            if len(encoded) > 16_384:
                raise ValueError("Host check observation exceeds export bound")
            if len(observations) >= 256:
                raise ValueError("Host tool observation count exceeds export bound")
            observations.append({"step_index": step.index, "request_id": step.request_id,
                                 "tool": call.get("tool"), "summary": fields})
    save(output / "host-tool-observations.json", observations)
    return observations


def _journey(workspace, identity, calibration, report, output, *, verify_method="ordinary_test"):
    report.update(scope="synthetic full admitted SymPy source public TaskRunner journey only",
                  taskrunner_wired=True, model_transport="scripted", historical_target_tests=False,
                  hidden_oracle="not_run", benchmark_quality_claim=False, whole30_qualified=False,
                  raw_in_loop_receipts_retained=False,
                  receipt_evidence="bounded host tool summaries; raw native executor proof is separate smoke evidence",
                  internal_verify_label=verify_method)
    previous = os.environ.get("ATOMIC_FLOW_HOME")
    state = workspace.parent / "journey-private-state"
    try:
        os.environ["ATOMIC_FLOW_HOME"] = str(state)
        sys.path.insert(0, str(ROOT / "tests"))
        from conftest import ScriptedBackend

        original = "value = 1\n"
        (workspace / MODULE_PATH).write_bytes(original.encode())
        (workspace / TEST_PATH).write_bytes(ORDINARY_TEST.encode())
        save_tree(workspace, build_tree(workspace))
        trust_project(workspace)
        before = check_source_snapshot(workspace)
        save(output / "source-before.json", before)
        spec = CheckSpec(task_id="p07_native_full_source_scripted_journey", task_commit=report["tested_commit"],
                         base_commit=calibration["base_commit"], source_revision=check_source_revision(workspace),
                         image=IMAGE, interpreter=INTERPRETER, runner="sympy-native", test_paths=(TEST_PATH,),
                         target_root="/testbed", sympy_identity=identity)
        obligation = CheckObligation.seal(spec, workspace, environment_digest=sha(IMAGE.encode()),
                                          deadline_at=time.time() + 900)
        save(output / "obligation.json", obligation.as_dict())
        step = {"description": "Repair the native ordinary requirement by changing only the probe module",
                "tools_needed": ["read_file", "patch_file", "run_command"], "verify_with": "pytest" if verify_method == "ordinary_test" else "ordinary_test",
                "scope_paths": [MODULE_PATH]}

        class Backend(ScriptedBackend):
            def chat(self, messages, **kwargs):
                if "<role>" not in messages[1]["content"]:
                    from atomic_flow.backends.base import ChatResult
                    self.calls.append(("host_planner", messages))
                    return ChatResult(json.dumps({"steps": [step], "rationale": "Repair completed native failure within original scope"}),
                                      100, 50, 0.001, 5)
                return super().chat(messages, **kwargs)

        backend = Backend({"parser": {"task_type": "modify", "focus_terms": ["value", "p07_probe"]},
            "answerer": [
                {"tool": "run_command", "args": {"command": "check"}},
                {"text": "The ordinary requirement failed and needs repair.", "confidence": "low"},
                {"tool": "read_file", "args": {"path": MODULE_PATH}},
                {"tool": "patch_file", "args": {"path": MODULE_PATH,
                    "expected_hash": hashlib.sha1(original.encode()).hexdigest(),
                    "old_text": original, "new_text": "value = 42\n"}},
                {"tool": "run_command", "args": {"command": "check"}},
                {"text": "The probe now satisfies the ordinary native check.", "confidence": "low"}],
            "critic": {"verdict": "ok", "citation_coverage": 1}})
        config = AtomicConfig()
        config.execution.mode, config.execution.profile, config.execution.image = "docker", "checks", IMAGE
        config.artifacts.save = True
        config.task.max_replans = 1
        config.task.max_model_calls = 40
        config.task.max_budget_usd = 1
        runner = TaskRunner(config, workspace, backend, approval_callback=lambda *args: True, check_obligation=obligation)
        task = runner.run_plan("Change only the probe module so the existing native ordinary test passes",
                               Plan(steps=[PlanStep(description="Check existing native requirement and repair if needed",
                                     tools_needed=["read_file", "patch_file", "run_command"], verify_with=verify_method,
                                     scope_paths=[MODULE_PATH])]), allowed_paths=[MODULE_PATH], allow_internal_replan=True)
        save(output / "task.json", asdict(task))
        after = check_source_snapshot(workspace)
        save(output / "source-after.json", after)
        changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
        observations = _observations(state, task, output)
        checks = [row["summary"] for row in observations if row["tool"] == "run_command"]
        negative = [row for row in checks if row.get("check_failed_completed") is True
                    and row.get("check_completed") is True and row.get("exit_code") == 1
                    and row.get("check_runner") == "sympy-native"
                    and row.get("check_obligation_digest") == obligation.digest]
        positive = [row for row in checks if row.get("check_passed") is True and row.get("check_completed") is True
                    and row.get("exit_code") == 0 and row.get("check_runner") == "sympy-native"
                    and row.get("check_obligation_digest") == obligation.digest]
        runner_unchanged = all(sha((workspace / path).read_bytes()) == digest for path, digest in identity.source_sha256)
        report.update(task_status=task.status, task_error=task.error, replan_count=task.replan_count,
                      completion=task.completion_report, obligation_digest=obligation.digest, graph_sync=task.graph_sync,
                      backend_roles=backend.roles(), changed_source_paths=changed,
                      admitted_source_files=len(before), source_manifest_scope="core admitted source filter; generated/hidden files excluded",
                      ordinary_test_sha256=sha((workspace / TEST_PATH).read_bytes()), runner_source_unchanged=runner_unchanged,
                      completed_native_failure_observations=len(negative), completed_native_pass_observations=len(positive),
                      published_module_sha256=sha((workspace / MODULE_PATH).read_bytes()))
        failures = [step.failure for step in task.steps if step.failure.get("kind") == "deterministic_test_failure"]
        if (task.status != "done" or task.replan_count != 1 or changed != [MODULE_PATH]
                or (workspace / MODULE_PATH).read_bytes() != b"value = 42\n"
                or (workspace / TEST_PATH).read_bytes() != ORDINARY_TEST.encode() or not runner_unchanged
                or not negative or not positive or not failures
                or negative[0].get("checked_source_revision") != spec.source_revision
                or positive[-1].get("checked_source_revision") != check_source_revision(workspace)
                or task.graph_sync.get("failed") is not False
                or task.graph_sync.get("coverage_complete") is not False
                or not any(row.get("path") == "sympy/integrals/rubi/rubi_tests/tests/test_trinomials.py"
                           for row in task.graph_sync.get("coverage_omissions", []))
                or not task.completion_report.get("ready_to_publish")
                or task.completion_report.get("check_runner") != "sympy-native"
                or task.completion_report.get("check_passed") is not True
                or task.completion_report.get("check_completed") is not True
                or task.completion_report.get("check_result_exit_code") != 0
                or task.completion_report.get("checked_source_revision") != check_source_revision(workspace)
                or task.completion_report.get("check_obligation_digest") != obligation.digest):
            raise ValueError("Full-source public native failure/replan/repair/check/publication journey did not qualify")
        report["status"] = QUALIFIED
    finally:
        if previous is None:
            os.environ.pop("ATOMIC_FLOW_HOME", None)
        else:
            os.environ["ATOMIC_FLOW_HOME"] = previous


def journey(output, verify_method="ordinary_test"):
    if verify_method not in {"ordinary_test", "pytest"}:
        raise ValueError("fixed host verification method required")
    return smoke(output, host_callback=lambda *args: _journey(*args, verify_method=verify_method))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-method", choices=("ordinary_test", "pytest"), default="ordinary_test")
    arguments = parser.parse_args()
    result = journey(arguments.output, arguments.verify_method)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == QUALIFIED else 1)
