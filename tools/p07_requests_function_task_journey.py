"""Complete public function36 ordinary suites with scripted TaskRunner repair.

All public original test files run, plus the fixed synthetic probe. No gold,
hidden test patch, live model or independent requirement grade is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path

from p07_requests_function_check_smoke import MODULE, ORDINARY, ROOT, TEST, encoded, save, sha, smoke
from p07_sympy_task_journey import _observations

from atomic_flow import check_requests_function_execution as execution
from atomic_flow.agents.failure_policy import validate_failure_evidence
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.agents.verify import check_source_revision, check_source_snapshot
from atomic_flow.check_execution import completed_check_receipt, run_private_syntax
from atomic_flow.check_obligation import CheckObligation
from atomic_flow.check_spec import CheckResources, CheckSpec
from atomic_flow.config import AtomicConfig, trust_project
from atomic_flow.tree.builder import build_tree, save_tree

QUALIFIED = "scripted_full_public_function36_taskrunner_qualified"


def validate(task, *, initial_spec, obligation, final_revision, changed, raw_checks, failures, final_module, final_test):
    if (task.status != "done" or task.replan_count != 1 or changed != [MODULE]
            or initial_spec.source_revision == final_revision
            or final_module != b"value = 42\n" or final_test != ORDINARY.encode() or not failures or len(raw_checks) < 2):
        return False
    ids, negative, positive = set(), False, False
    for spec, receipt, topology, proof in raw_checks:
        if type(proof) is not dict or type(proof.get("resource_container")) is not dict:
            return False
        source_cid = proof["resource_container"].get("container_id")
        if (type(source_cid) is not str or re.fullmatch(r"[a-f0-9]{64}", source_cid) is None
                or source_cid in ids or not completed_check_receipt(spec, receipt) or topology.container_id in ids
                or topology.nonce != receipt.runner_receipt["nonce"] or topology.netns_inode == topology.host_netns_inode
                or topology.image_id != spec.image or topology.fixture_digest != spec.fixture_identity.digest
                or proof.get("candidate_ca_present") is not False or proof.get("candidate_and_execution_specs_equal") is not True
                or proof.get("candidate_core_revision") != spec.source_revision
                or proof.get("execution_core_revision") != spec.source_revision
                or proof.get("overlay") != receipt.runner_receipt["requests_function_fixture"]["execution_overlay"]
                or proof.get("resource_container", {}).get("image_id") != spec.image
                or proof.get("resource_container", {}).get("started") is not False
                or proof.get("resource_container", {}).get("container_id") == topology.container_id
                or proof.get("resource_container", {}).get("cleanup_confirmed") is not True):
            return False
        ids.update((topology.container_id, source_cid))
        negative |= completed_check_receipt(spec, receipt, negative=True) and spec.source_revision == initial_spec.source_revision
        positive |= receipt.check_passed and receipt.result.returncode == 0 and spec.source_revision == final_revision
    completion, graph = task.completion_report, task.graph_sync
    final_spec, final_receipt, _, _ = raw_checks[-1]
    final_receipt_digest = hashlib.sha256(json.dumps(final_receipt.runner_receipt, sort_keys=True, separators=(",", ":"),
                                                     allow_nan=False).encode()).hexdigest()
    if (not negative or not positive or completion.get("ready_to_publish") is not True
            or completion.get("check_passed") is not True or completion.get("check_completed") is not True
            or completion.get("check_runner") != "pytest" or completion.get("check_result_exit_code") != 0
            or completion.get("checked_source_revision") != final_revision
            or final_spec.source_revision != final_revision or final_receipt.check_passed is not True
            or completion.get("check_spec_digest") != final_spec.digest
            or completion.get("check_receipt_digest") != final_receipt_digest
            or completion.get("check_obligation_digest") != obligation.digest or graph.get("failed") is not False
            or type(graph.get("coverage_complete")) is not bool or type(graph.get("coverage_omissions")) is not list
            or graph["coverage_complete"] != (not graph["coverage_omissions"])):
        return False
    for failure in failures:
        proof = failure.get("evidence", {})
        try:
            validate_failure_evidence(proof)
        except ValueError:
            return False
        if (proof.get("version") != 2 or proof.get("check") != "ordinary_test" or proof.get("runner") != "pytest"
                or proof.get("source_revision") != initial_spec.source_revision
                or proof.get("checked_source_revision") != initial_spec.source_revision
                or proof.get("check_spec_digest") != initial_spec.digest or proof.get("check_obligation_digest") != obligation.digest):
            return False
    return True


def _journey(candidate, runtime, identity, fixture, ca, target, report, output):
    report.update(scope="complete public function36 ordinary inventory plus synthetic probe repair",
                  taskrunner_wired=True, generic_dispatch_enabled=True, historical_target_tests=True,
                  model_transport="scripted", hidden_oracle="not_run", whole30_qualified=False)
    previous_home, old_path = os.environ.get("ATOMIC_FLOW_HOME"), list(sys.path)
    original_execute = execution.run_function36_check
    state, raw_checks = candidate.parent / "function-task-state", []

    def recording(spec, root, *, cancel_check=None):
        before = check_source_snapshot(root)
        try:
            receipt, topology, resources = original_execute(spec, root, cancel_check=cancel_check)
        except Exception as error:
            unavailable = {"check_spec_digest": spec.digest, "source_revision": spec.source_revision,
                           "error_type": type(error).__name__, "error": str(error)[:500],
                           "controller_returned_after_cleanup": False}
            report.setdefault("task_controller_errors", []).append(unavailable)
            save(output / ("task-check-" + str(len(raw_checks)).zfill(3) + "-unavailable.json"), unavailable)
            raise
        case = output / ("task-check-" + str(len(raw_checks)).zfill(3))
        case.mkdir()
        save(case / "spec.json", spec.as_dict())
        save(case / "receipt.json", receipt.as_dict())
        save(case / "topology.json", topology.as_dict())
        save(case / "resources.json", resources)
        save(case / "candidate-before.json", before)
        save(case / "candidate-after.json", check_source_snapshot(root))
        (case / "stdout.txt").write_bytes(receipt.result.stdout.encode())
        (case / "stderr.txt").write_bytes(receipt.result.stderr.encode())
        raw_checks.append((spec, receipt, topology, resources))
        return receipt, topology, resources

    try:
        os.environ["ATOMIC_FLOW_HOME"] = str(state)
        sys.path.insert(0, str(ROOT / "tests"))
        from conftest import ScriptedBackend

        original = "value = 1\n"
        (candidate / MODULE).write_bytes(original.encode())
        (candidate / TEST).write_bytes(ORDINARY.encode())
        save_tree(candidate, build_tree(candidate))
        trust_project(candidate)
        before = check_source_snapshot(candidate)
        public_paths = tuple(sorted(p for p in before if Path(p).name.startswith("test_") and p.endswith(".py") and p != TEST))
        # The public frozen base trees of all three tasks expose this one file.
        # Refuse unexpected trees rather than selecting a convenient subset.
        if public_paths != ("test_requests.py",):
            raise ValueError("complete public ordinary test inventory differs from frozen source trees")
        paths = tuple(sorted((*public_paths, TEST)))
        report["ordinary_test_paths"] = list(paths)
        save(output / "task-source-before.json", before)
        spec = CheckSpec(identity.task_id, report["tested_commit"], target["base_commit"], check_source_revision(candidate),
            runtime.image_id, runtime.interpreter, "pytest", paths, target_root="/testbed", runtime_identity=runtime,
            private_pytest_identity=identity, fixture_identity=fixture, requests_ca_identity=ca,
            resources=CheckResources(output_bytes_per_stream=1_000_000))
        syntax = run_private_syntax(spec, candidate, MODULE)
        save(output / "target-syntax.json", syntax)
        if syntax.get("outcome") != "passed" or syntax.get("python_version") != [3, 6, 15]:
            raise ValueError("actual private Python3.6 syntax precheck required")
        environment = sha(encoded({"runtime": runtime.digest, "fixture": fixture.digest, "resources": ca.digest}))
        obligation = CheckObligation.seal(spec, candidate, environment_digest=environment, deadline_at=time.time() + 1500)
        save(output / "task-obligation.json", obligation.as_dict())
        replacement = {"description": "Repair the completed ordinary probe failure within original scope",
                       "tools_needed": ["read_file", "patch_file", "run_command"], "verify_with": "pytest", "scope_paths": [MODULE]}

        class Backend(ScriptedBackend):
            def chat(self, messages, **kwargs):
                if "<role>" not in messages[1]["content"]:
                    from atomic_flow.backends.base import ChatResult
                    self.calls.append(("host_planner", messages))
                    return ChatResult(json.dumps({"steps": [replacement], "rationale": "Repair completed ordinary failure"}),
                                      100, 50, 0.001, 5)
                return super().chat(messages, **kwargs)

        backend = Backend({"parser": {"task_type": "modify", "focus_terms": ["value", "p07_function_probe"]},
                           "answerer": [
                               {"tool": "run_command", "args": {"command": "check"}},
                               {"text": "The ordinary requirement failed and needs repair.", "confidence": "low"},
                               {"tool": "read_file", "args": {"path": MODULE}},
                               {"tool": "patch_file", "args": {"path": MODULE,
                                "expected_hash": hashlib.sha1(original.encode()).hexdigest(),
                                "old_text": original, "new_text": "value = 42\n"}},
                               {"tool": "run_command", "args": {"command": "check"}},
                               {"text": "The public ordinary requirement passes.", "confidence": "low"}],
                           "critic": {"verdict": "ok", "citation_coverage": 1}})
        config = AtomicConfig()
        config.execution.mode, config.execution.profile, config.execution.image = "docker", "checks", runtime.image_id
        config.artifacts.save = True
        config.task.max_replans, config.task.max_model_calls, config.task.max_budget_usd = 1, 40, 1
        execution.run_function36_check = recording
        runner = TaskRunner(config, candidate, backend, approval_callback=lambda *args: True, check_obligation=obligation)
        task = runner.run_plan("Change only the probe module so the complete public ordinary suite passes",
            Plan(steps=[PlanStep(description="Check the existing ordinary requirement and repair if needed",
                tools_needed=["read_file", "patch_file", "run_command"], verify_with="ordinary_test", scope_paths=[MODULE])]),
            allowed_paths=[MODULE], allow_internal_replan=True)
        save(output / "task.json", asdict(task))
        after = check_source_snapshot(candidate)
        save(output / "task-source-after.json", after)
        changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
        _observations(state, task, output)
        failures = [step.failure for step in task.steps if step.failure.get("kind") == "deterministic_test_failure"]
        report.update(task_status=task.status, task_error=task.error, replan_count=task.replan_count,
                      completion=task.completion_report, graph_sync=task.graph_sync, raw_in_loop_checks=len(raw_checks),
                      changed_source_paths=changed, backend_roles=backend.roles(), failure_v2=failures,
                      obligation_digest=obligation.digest, admitted_core_files=len(before), initial_target_syntax=syntax)
        if not validate(task, initial_spec=spec, obligation=obligation, final_revision=check_source_revision(candidate),
                        changed=changed, raw_checks=raw_checks, failures=failures,
                        final_module=(candidate / MODULE).read_bytes(), final_test=(candidate / TEST).read_bytes()):
            raise ValueError("complete public function36 ordinary TaskRunner journey did not qualify")
        report["status"] = QUALIFIED
    finally:
        execution.run_function36_check = original_execute
        sys.path[:] = old_path
        if previous_home is None:
            os.environ.pop("ATOMIC_FLOW_HOME", None)
        else:
            os.environ["ATOMIC_FLOW_HOME"] = previous_home


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = smoke(args.runtime_receipt, args.output, host_callback=_journey)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == QUALIFIED else 1)
