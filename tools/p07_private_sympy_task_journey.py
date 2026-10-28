"""Scripted full-source private Python3.6 native TaskRunner journey.

Only public built-in tools run against synthetic ordinary tests. Provider calls
are zero. This is not historical issue success or whole-30 model quality.
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

from p07_private_sympy_check_smoke import MODULE_PATH, ORDINARY_TEST, ROOT, TEST_PATH, save, sha, smoke
from p07_sympy_task_journey import _observations

from atomic_flow.agents.failure_policy import validate_failure_evidence
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.agents.verify import check_source_revision, check_source_snapshot
from atomic_flow.check_execution import run_private_syntax
from atomic_flow.check_obligation import CheckObligation
from atomic_flow.check_spec import CheckSpec
from atomic_flow.config import AtomicConfig, trust_project
from atomic_flow.tree.builder import build_tree, save_tree

QUALIFIED = "scripted_full_source_private_sympy_task_journey_qualified"


def validate_journey(task, *, changed, negative, positive, failures, spec, obligation,
                     final_revision, runner_unchanged, final_module, final_test):
    """Fail closed on publication, source, native-check and failure-v2 bindings."""
    graph = task.graph_sync
    omissions = graph.get("coverage_omissions")
    if (task.status != "done" or task.replan_count != 1 or changed != [MODULE_PATH]
            or final_module != b"value = 42\n" or final_test != ORDINARY_TEST.encode() or not runner_unchanged
            or not negative or not positive or not failures
            or negative[0].get("checked_source_revision") != spec.source_revision
            or positive[-1].get("checked_source_revision") != final_revision
            or graph.get("failed") is not False or type(graph.get("coverage_complete")) is not bool
            or type(omissions) is not list or graph["coverage_complete"] != (not omissions)):
        return False
    completion = task.completion_report
    if (not completion.get("ready_to_publish") or completion.get("check_runner") != "sympy-native"
            or completion.get("check_passed") is not True or completion.get("check_completed") is not True
            or completion.get("check_result_exit_code") != 0
            or completion.get("checked_source_revision") != final_revision
            or completion.get("check_obligation_digest") != obligation.digest):
        return False
    for failure in failures:
        proof = failure.get("evidence", {})
        try:
            validate_failure_evidence(proof)
        except ValueError:
            return False
        if (proof.get("version") != 2 or proof.get("check") != "ordinary_test"
                or proof.get("runner") != "sympy-native" or proof.get("source_revision") != spec.source_revision
                or proof.get("checked_source_revision") != spec.source_revision
                or proof.get("check_spec_digest") != spec.digest or proof.get("check_obligation_digest") != obligation.digest):
            return False
    return True


def _journey(workspace, identity, runtime, admitted, report, output, *, verify_method="ordinary_test"):
    report.update(scope="synthetic full admitted private SymPy source public TaskRunner journey only",
                  taskrunner_wired=True, model_transport="scripted", provider_calls=0, model_calls=0,
                  historical_target_tests=False, hidden_oracle="not_run", benchmark_quality_claim=False,
                  whole30_qualified=False, internal_verify_label=verify_method,
                  raw_in_loop_receipts_retained=False,
                  receipt_evidence="bounded host tool summaries; separate admitted private executor smoke retains raw native receipts")
    previous = os.environ.get("ATOMIC_FLOW_HOME")
    old_sys_path = list(sys.path)
    state = workspace.parent / "private-journey-state"
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
        spec = CheckSpec(task_id="p07_private_native_full_source_scripted_journey", task_commit=report["tested_commit"],
                         base_commit=admitted["profile"]["task"]["base_commit"], source_revision=check_source_revision(workspace),
                         image=runtime.image_id, interpreter=runtime.interpreter, runner="sympy-native", test_paths=(TEST_PATH,),
                         target_root="/testbed", sympy_identity=identity, runtime_identity=runtime)
        syntax = run_private_syntax(spec, workspace, MODULE_PATH)
        save(output / "initial-target-syntax-receipt.json", syntax)
        if syntax.get("outcome") != "passed" or syntax.get("python_version") != [3, 6, 15]:
            raise ValueError("Actual private Python3.6 target syntax precheck required")
        obligation = CheckObligation.seal(spec, workspace, environment_digest=runtime.digest, deadline_at=time.time() + 1200)
        save(output / "obligation.json", obligation.as_dict())
        replacement = {"description": "Repair the native ordinary requirement by changing only the probe module",
                       "tools_needed": ["read_file", "patch_file", "run_command"],
                       "verify_with": "pytest" if verify_method == "ordinary_test" else "ordinary_test",
                       "scope_paths": [MODULE_PATH]}

        class Backend(ScriptedBackend):
            def chat(self, messages, **kwargs):
                if "<role>" not in messages[1]["content"]:
                    from atomic_flow.backends.base import ChatResult
                    self.calls.append(("host_planner", messages))
                    return ChatResult(json.dumps({"steps": [replacement], "rationale": "Repair completed native failure within original scope"}),
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
                               {"text": "The probe satisfies the private native check.", "confidence": "low"}],
                           "critic": {"verdict": "ok", "citation_coverage": 1}})
        config = AtomicConfig()
        config.execution.mode, config.execution.profile, config.execution.image = "docker", "checks", runtime.image_id
        config.artifacts.save = True
        config.task.max_replans = 1
        config.task.max_model_calls = 40
        config.task.max_budget_usd = 1
        runner = TaskRunner(config, workspace, backend, approval_callback=lambda *args: True, check_obligation=obligation)
        task = runner.run_plan("Change only the probe module so the existing private native ordinary test passes",
                               Plan(steps=[PlanStep(description="Check existing private native requirement and repair if needed",
                                     tools_needed=["read_file", "patch_file", "run_command"], verify_with=verify_method,
                                     scope_paths=[MODULE_PATH])]), allowed_paths=[MODULE_PATH], allow_internal_replan=True)
        save(output / "task.json", asdict(task))
        after = check_source_snapshot(workspace)
        save(output / "source-after.json", after)
        changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
        observations = _observations(state, task, output)
        checks = [row["summary"] for row in observations if row["tool"] == "run_command"]
        negative = [row for row in checks if row.get("check_failed_completed") is True and row.get("check_completed") is True
                    and row.get("exit_code") == 1 and row.get("check_runner") == "sympy-native"
                    and row.get("check_obligation_digest") == obligation.digest]
        positive = [row for row in checks if row.get("check_passed") is True and row.get("check_completed") is True
                    and row.get("exit_code") == 0 and row.get("check_runner") == "sympy-native"
                    and row.get("check_obligation_digest") == obligation.digest]
        immutable = all(sha((workspace / path).read_bytes()) == digest for path, digest in identity.source_sha256)
        failures = [step.failure for step in task.steps if step.failure.get("kind") == "deterministic_test_failure"]
        report.update(task_status=task.status, task_error=task.error, replan_count=task.replan_count,
                      completion=task.completion_report, obligation_digest=obligation.digest, graph_sync=task.graph_sync,
                      backend_roles=backend.roles(), changed_source_paths=changed, admitted_source_files=len(before),
                      source_manifest_scope="core admitted source filter; generated/hidden files excluded",
                      ordinary_test_sha256=sha((workspace / TEST_PATH).read_bytes()), runner_source_unchanged=immutable,
                      completed_native_failure_observations=len(negative), completed_native_pass_observations=len(positive),
                      published_module_sha256=sha((workspace / MODULE_PATH).read_bytes()),
                      initial_target_syntax=syntax, failure_v2=failures)
        if not validate_journey(task, changed=changed, negative=negative, positive=positive, failures=failures,
                                spec=spec, obligation=obligation, final_revision=check_source_revision(workspace),
                                runner_unchanged=immutable, final_module=(workspace / MODULE_PATH).read_bytes(),
                                final_test=(workspace / TEST_PATH).read_bytes()):
            raise ValueError("Full-source private native TaskRunner journey did not qualify")
        report["status"] = QUALIFIED
    finally:
        sys.path[:] = old_sys_path
        if previous is None:
            os.environ.pop("ATOMIC_FLOW_HOME", None)
        else:
            os.environ["ATOMIC_FLOW_HOME"] = previous


def journey(runtime_receipt, runtime_context, output, verify_method="ordinary_test"):
    if verify_method not in {"ordinary_test", "pytest"}:
        raise ValueError("fixed host verification method required")
    return smoke(runtime_receipt, runtime_context, output,
                 host_callback=lambda *args: _journey(*args, verify_method=verify_method))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-receipt", type=Path, required=True)
    parser.add_argument("--runtime-context", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-method", choices=("ordinary_test", "pytest"), default="ordinary_test")
    args = parser.parse_args()
    result = journey(args.runtime_receipt, args.runtime_context, args.output, args.verify_method)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == QUALIFIED else 1)
