"""Full-source scripted Requests3362 TaskRunner with actual fixture checks.

Synthetic ordinary tests only. No historical hidden test, gold patch or model
call is used. Raw controller receipts and immutable namespace IDs are retained.
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

from p07_requests3362_check_smoke import MODULE_PATH, ORDINARY_SOURCE, ROOT, TEST_PATH, WRONG_CA, save, smoke
from p07_sympy_task_journey import _observations

from atomic_flow import check_requests_execution as controller
from atomic_flow.agents.failure_policy import validate_failure_evidence
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.agents.verify import check_source_revision, check_source_snapshot
from atomic_flow.check_execution import completed_check_receipt, run_private_syntax
from atomic_flow.check_obligation import CheckObligation
from atomic_flow.check_spec import CheckResources, CheckSpec
from atomic_flow.config import AtomicConfig, trust_project
from atomic_flow.tree.builder import build_tree, save_tree

QUALIFIED = "scripted_full_source_requests3362_task_journey_qualified"
PUBLIC_CA_PATH = "p07_public_wrongca.txt"
JOURNEY_TEST_SOURCE = ORDINARY_SOURCE.replace("/testbed/" + WRONG_CA, "/testbed/" + PUBLIC_CA_PATH)


def validate_journey(task, *, before_spec, obligation, final_revision, changed, failures, raw_checks,
                     final_module, final_test):
    """Retained ordinary execution/publication facts, never a hidden grade."""
    if (task.status != "done" or task.replan_count != 1 or changed != [MODULE_PATH]
            or final_module != b"value = 42\n" or final_test != JOURNEY_TEST_SOURCE.encode()
            or not failures or len(raw_checks) < 2):
        return False
    negative, positive = False, False
    ids = set()
    for spec, receipt, topology in raw_checks:
        if (topology.container_id in ids or topology.host_netns_inode == topology.netns_inode
                or receipt.source_before != receipt.source_after or receipt.source_before != spec.source_revision
                or not completed_check_receipt(spec, receipt)):
            return False
        ids.add(topology.container_id)
        negative |= (completed_check_receipt(spec, receipt, negative=True)
                     and spec.source_revision == before_spec.source_revision)
        positive |= receipt.check_passed and spec.source_revision == final_revision
    if not negative or not positive:
        return False
    completion = task.completion_report
    graph = task.graph_sync
    if (completion.get("ready_to_publish") is not True or completion.get("check_passed") is not True
            or completion.get("check_completed") is not True or completion.get("check_result_exit_code") != 0
            or completion.get("check_runner") != "pytest" or completion.get("checked_source_revision") != final_revision
            or completion.get("check_obligation_digest") != obligation.digest or graph.get("failed") is not False
            or type(graph.get("coverage_omissions")) is not list
            or graph.get("coverage_complete") != (not graph["coverage_omissions"])):
        return False
    for failure in failures:
        proof = failure.get("evidence", {})
        try:
            validate_failure_evidence(proof)
        except ValueError:
            return False
        if (proof.get("version") != 2 or proof.get("runner") != "pytest"
                or proof.get("check") != "ordinary_test" or proof.get("source_revision") != before_spec.source_revision
                or proof.get("checked_source_revision") != before_spec.source_revision
                or proof.get("check_spec_digest") != before_spec.digest
                or proof.get("check_obligation_digest") != obligation.digest):
            return False
    return True


def _journey(workspace, runtime, identity, fixture, report, output):
    report.update(scope="synthetic full admitted Requests3362 public TaskRunner journey",
                  taskrunner_wired=True, generic_dispatch_enabled=True, model_transport="scripted",
                  historical_target_tests=False, hidden_oracle="not_run", whole30_qualified=False)
    previous_home = os.environ.get("ATOMIC_FLOW_HOME")
    old_path = list(sys.path)
    original_controller = controller.run_requests3362_check
    state = workspace.parent / "requests-journey-state"
    raw_checks = []

    def recording(spec, root, *, cancel_check=None):
        receipt, topology = original_controller(spec, root, cancel_check=cancel_check)
        case = output / ("task-check-" + str(len(raw_checks)).zfill(3))
        case.mkdir()
        save(case / "spec.json", spec.as_dict())
        save(case / "receipt.json", receipt.as_dict())
        save(case / "topology.json", topology.as_dict())
        (case / "stdout.txt").write_bytes(receipt.result.stdout.encode())
        (case / "stderr.txt").write_bytes(receipt.result.stderr.encode())
        raw_checks.append((spec, receipt, topology))
        return receipt, topology

    try:
        os.environ["ATOMIC_FLOW_HOME"] = str(state)
        sys.path.insert(0, str(ROOT / "tests"))
        from conftest import ScriptedBackend

        original = "value = 1\n"
        (workspace / MODULE_PATH).write_bytes(original.encode())
        # Explicit disclosed synthetic public CA data; the generic source
        # filter continues excluding PEM/key files from TaskRunner workspaces.
        (workspace / PUBLIC_CA_PATH).write_bytes((workspace / WRONG_CA).read_bytes())
        (workspace / TEST_PATH).write_bytes(JOURNEY_TEST_SOURCE.encode())
        save_tree(workspace, build_tree(workspace))
        trust_project(workspace)
        before = check_source_snapshot(workspace)
        save(output / "task-source-before.json", before)
        spec = CheckSpec(runtime_identity=runtime, private_pytest_identity=identity, fixture_identity=fixture,
                         task_id=identity.task_id, task_commit=report["tested_commit"],
                         base_commit="36453b95b13079296776d11b09cab2567ea3e703", source_revision=check_source_revision(workspace),
                         image=runtime.image_id, interpreter=runtime.interpreter, runner="pytest", test_paths=(TEST_PATH,),
                         target_root="/testbed", resources=CheckResources(output_bytes_per_stream=1_000_000))
        syntax = run_private_syntax(spec, workspace, MODULE_PATH)
        save(output / "target-syntax.json", syntax)
        if syntax.get("outcome") != "passed" or syntax.get("python_version") != [3, 6, 15]:
            raise ValueError("Actual private target grammar precheck required")
        obligation = CheckObligation.seal(spec, workspace, environment_digest=fixture.digest,
                                         deadline_at=time.time() + 1200, protected_paths=(PUBLIC_CA_PATH,))
        save(output / "task-obligation.json", obligation.as_dict())
        replacement = {"description": "Repair the completed ordinary probe failure within original scope",
                       "tools_needed": ["read_file", "patch_file", "run_command"],
                       "verify_with": "pytest", "scope_paths": [MODULE_PATH]}

        class Backend(ScriptedBackend):
            def chat(self, messages, **kwargs):
                if "<role>" not in messages[1]["content"]:
                    from atomic_flow.backends.base import ChatResult
                    self.calls.append(("host_planner", messages))
                    return ChatResult(json.dumps({"steps": [replacement], "rationale": "Repair completed ordinary failure"}),
                                      100, 50, 0.001, 5)
                return super().chat(messages, **kwargs)

        backend = Backend({"parser": {"task_type": "modify", "focus_terms": ["value", "p07_private_probe"]},
                           "answerer": [
                               {"tool": "run_command", "args": {"command": "check"}},
                               {"text": "The ordinary requirement failed and needs repair.", "confidence": "low"},
                               {"tool": "read_file", "args": {"path": MODULE_PATH}},
                               {"tool": "patch_file", "args": {"path": MODULE_PATH,
                                "expected_hash": hashlib.sha1(original.encode()).hexdigest(),
                                "old_text": original, "new_text": "value = 42\n"}},
                               {"tool": "run_command", "args": {"command": "check"}},
                               {"text": "The probe satisfies the ordinary requirement.", "confidence": "low"}],
                           "critic": {"verdict": "ok", "citation_coverage": 1}})
        config = AtomicConfig()
        config.execution.mode, config.execution.profile, config.execution.image = "docker", "checks", runtime.image_id
        config.artifacts.save = True
        config.task.max_replans, config.task.max_model_calls, config.task.max_budget_usd = 1, 40, 1
        controller.run_requests3362_check = recording
        runner = TaskRunner(config, workspace, backend, approval_callback=lambda *args: True, check_obligation=obligation)
        task = runner.run_plan("Change only the probe module so the existing ordinary test passes",
                               Plan(steps=[PlanStep(description="Check existing ordinary requirement and repair if needed",
                                     tools_needed=["read_file", "patch_file", "run_command"], verify_with="ordinary_test",
                                     scope_paths=[MODULE_PATH])]), allowed_paths=[MODULE_PATH], allow_internal_replan=True)
        save(output / "task.json", asdict(task))
        after = check_source_snapshot(workspace)
        save(output / "task-source-after.json", after)
        changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
        _observations(state, task, output)
        failures = [step.failure for step in task.steps if step.failure.get("kind") == "deterministic_test_failure"]
        report.update(task_status=task.status, task_error=task.error, replan_count=task.replan_count,
                      completion=task.completion_report, graph_sync=task.graph_sync, obligation_digest=obligation.digest,
                      backend_roles=backend.roles(), changed_source_paths=changed, admitted_source_files=len(before),
                      raw_in_loop_checks=len(raw_checks), initial_target_syntax=syntax, failure_v2=failures)
        if not validate_journey(task, before_spec=spec, obligation=obligation, final_revision=check_source_revision(workspace),
                                changed=changed, failures=failures, raw_checks=raw_checks,
                                final_module=(workspace / MODULE_PATH).read_bytes(), final_test=(workspace / TEST_PATH).read_bytes()):
            raise ValueError("Requests3362 full TaskRunner journey did not qualify")
        report["status"] = QUALIFIED
    finally:
        controller.run_requests3362_check = original_controller
        sys.path[:] = old_path
        if previous_home is None:
            os.environ.pop("ATOMIC_FLOW_HOME", None)
        else:
            os.environ["ATOMIC_FLOW_HOME"] = previous_home


def journey(certificate_receipt, health_receipt, output):
    return smoke(certificate_receipt, health_receipt, output, host_callback=_journey)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate-receipt", type=Path, required=True)
    parser.add_argument("--health-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = journey(args.certificate_receipt, args.health_receipt, args.output)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == QUALIFIED else 1)
