"""Small deterministic audit probes; local synthetic files, no model/network calls.

Run from repository root with .venv/Scripts/python.exe docs/audit-2026-10-23/runtime_probes.py.
Interleaving is injected at the real Path.replace boundary, not a probabilistic stress test.
"""
import inspect
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from atomic_flow.agents.changeset import ChangeSet
from atomic_flow.agents.control import task_lease
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.llm.orchestrator import Orchestrator
from atomic_flow.sessions import SessionJournal


def run():
    results = {"network_calls": 0, "model_calls": 0}
    # Use an ordinary workspace directory; Windows tempfile ACL behaviour differs.
    root = Path(__file__).resolve().parent / ("runtime-evidence-" + next(tempfile._get_candidate_names()))
    root.mkdir()
    os.environ["ATOMIC_FLOW_HOME"] = str(root / "home")
    # Precreate under inherited workspace ACLs for the Windows restricted runner.
    (root / "home" / "sessions").mkdir(parents=True)
    project = root / "project"
    project.mkdir()
    target = project / "sample.py"
    target.write_text("value = 'baseline'\n", encoding="utf-8")
    a, b = ChangeSet.create(project), ChangeSet.create(project)
    for change, value in [(a, "A"), (b, "B")]:
        (change.workspace / "sample.py").write_text(f"value = '{value}'\n", encoding="utf-8")
        change.track("sample.py")
    original_replace = Path.replace
    injected = False

    def interleave(source, destination):
        nonlocal injected
        if Path(destination) == target and not injected:
            injected = True
            # A has checked baseline; B publishes fully before A's replace syscall.
            with task_lease("task_bbbbbbbbbbbb"):
                b.publish()
        return original_replace(source, destination)

    with task_lease("task_aaaaaaaaaaaa"), patch.object(Path, "replace", interleave):
        a.publish()
    results["interleaved_publication"] = {
        "injected_at_real_replace_boundary": injected,
        "a_status": a.state["status"], "b_status": b.state["status"],
        "final_source": target.read_text(encoding="utf-8"),
        "distinct_task_leases_both_acquired": True,
        "b_returned_success_but_its_source_overwritten": b.state["status"] == "published" and "'B'" not in target.read_text(),
    }

    journal = SessionJournal.create(project, "audit_unresolved")
    journal.append("model_request", {}, call_id="call_1")
    journal.append("model_failed", {}, call_id="call_1", outcome="unknown")
    journal.append("model_response", {"cost_usd": 0.01}, call_id="call_1", outcome="success")
    results["later_known_response"] = {"pending": journal.read().pending_call_ids,
                                        "unresolved": journal.read().unresolved_call_ids}
    journal.append("tool_effect_unknown", {}, call_id="effect_1", outcome="unknown")
    journal.append("tool_result", {"result": {"side_effects_may_have_occurred": False}}, call_id="effect_1", outcome="success")
    results["later_successful_tool_result"] = {"unresolved": journal.read().unresolved_call_ids}

    counts = []
    for n in [20, 40, 80]:
        journal = SessionJournal.create(project, f"audit_scale_{n}")
        validations = 0
        original_validate = journal._validate_event

        def counted(*args):
            nonlocal validations
            validations += 1
            return original_validate(*args)

        with patch.object(journal, "_validate_event", counted):
            for i in range(n):
                journal.append("user_input", {"text": f"synthetic event {i}"})
        counts.append({"appends": n, "prior_record_validations": validations,
                       "expected_triangular": n * (n + 1) // 2})
    results["journal_append_work"] = counts
    runner = inspect.signature(TaskRunner.__init__).parameters
    orch = inspect.signature(Orchestrator.__init__).parameters
    capabilities = ["workspace_session", "lsp_client", "documentation_client", "hooks", "tool_registry", "role_tools"]
    results["capability_injection"] = {k: {"orchestrator": k in orch, "task_runner": k in runner} for k in capabilities}
    results["evidence_directory"] = root.name
    output = Path(__file__).with_name("runtime-probes.json")
    output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    run()
