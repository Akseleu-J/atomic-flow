"""Real Docker scripted public TaskRunner journey; no live model or hidden oracle."""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from p07_check_spec_smoke import IMAGE, INTERPRETER

from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.agents.verify import check_source_revision
from atomic_flow.check_obligation import CheckObligation
from atomic_flow.check_spec import CheckSpec
from atomic_flow.config import AtomicConfig, trust_project
from atomic_flow.tree.builder import build_tree, save_tree

ROOT = Path(__file__).resolve().parents[1]


def journey(output):
    if platform.system() != "Linux" or output.exists():
        raise ValueError("fresh output on accepted Linux Docker host required")
    output.mkdir(parents=True)
    report = {"status": "not_qualified", "provider_calls": 0, "model_transport": "scripted",
              "independent_requirement_success": False, "hidden_oracle": "not_run"}
    previous = os.environ.get("ATOMIC_FLOW_HOME")
    try:
        subprocess.run(["docker", "pull", IMAGE], check=True, timeout=600)
        sys.path.insert(0, str(ROOT / "tests"))
        from conftest import ScriptedBackend

        with tempfile.TemporaryDirectory(prefix="p07-public-obligation-") as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            os.environ["ATOMIC_FLOW_HOME"] = str(root / "private-state")
            (project / "sample.py").write_text("def sample():\n    return 1\n")
            (project / "tests").mkdir()
            (project / "tests/test_sample.py").write_text("from sample import sample\ndef test_sample():\n    assert sample() == 42\n")
            save_tree(project, build_tree(project))
            trust_project(project)
            spec = CheckSpec(task_id="public-scripted-journey", task_commit="a" * 40, base_commit="b" * 40,
                             source_revision=check_source_revision(project), image=IMAGE, interpreter=INTERPRETER,
                             runner="pytest", test_paths=("tests/test_sample.py",))
            obligation = CheckObligation.seal(spec, project, environment_digest="d" * 64, deadline_at=time.time() + 600)
            step = {"description": "Repair sample return and verify original requirement", "tools_needed": ["read_node", "edit_node", "run_command"],
                    "verify_with": "pytest", "scope_paths": ["sample.py"]}
            class Backend(ScriptedBackend):
                def chat(self, messages, **kwargs):
                    if "<role>" not in messages[1]["content"]:
                        from atomic_flow.backends.base import ChatResult
                        self.calls.append(("host_planner", messages))
                        return ChatResult(json.dumps({"steps": [step], "rationale": "Repair failed ordinary requirement under original scope"}),
                                          100, 50, 0.001, 5)
                    return super().chat(messages, **kwargs)
            backend = Backend({"parser": {"task_type": "modify", "focus_terms": ["sample"]},
                "answerer": [
                    {"tool": "run_command", "args": {"command": "pytest"}},
                    {"text": "The source check failed; repair is required.", "confidence": "low"},
                    {"tool": "read_node", "args": {"node_id": "sample.py::sample", "zoom": 2}},
                    {"tool": "edit_node", "args": {"node_id": "sample.py::sample", "new_source": "def sample():\n    return 42"}},
                    {"tool": "run_command", "args": {"command": "pytest"}},
                    {"text": "Sample repaired and ordinary checks completed.", "confidence": "low"}],
                "critic": {"verdict": "ok", "citation_coverage": 1}})
            config = AtomicConfig()
            config.execution.mode, config.execution.profile, config.execution.image = "docker", "checks", IMAGE
            config.task.max_replans = 1
            config.task.max_model_calls = 40
            config.task.max_budget_usd = 1
            runner = TaskRunner(config, project, backend, approval_callback=lambda *args: True, check_obligation=obligation)
            task = runner.run_plan("Verify and repair sample so its existing ordinary test passes",
                                   Plan(steps=[PlanStep(description="Check sample requirement and repair if needed",
                                         tools_needed=["run_command"], verify_with="pytest", scope_paths=["sample.py"])]),
                                   allowed_paths=["sample.py"], allow_internal_replan=True)
            (output / "task.json").write_text(json.dumps(asdict(task), indent=2) + "\n")
            report.update(task_status=task.status, task_error=task.error, replan_count=task.replan_count,
                          completion=task.completion_report, backend_roles=backend.roles(),
                          final_source=(project / "sample.py").read_text(), obligation_digest=obligation.digest)
            if (task.status != "done" or task.replan_count != 1 or "return 42" not in report["final_source"]
                    or not task.completion_report.get("ready_to_publish")
                    or task.completion_report.get("check_obligation_digest") != obligation.digest):
                raise ValueError("public source-bound failure/repair/publication journey did not qualify")
            report["status"] = "scripted_public_obligation_journey_qualified"
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    finally:
        if previous is None:
            os.environ.pop("ATOMIC_FLOW_HOME", None)
        else:
            os.environ["ATOMIC_FLOW_HOME"] = previous
        (output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    result = journey(parser.parse_args().output)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "scripted_public_obligation_journey_qualified" else 1)
