"""P06 §7.7 scripted journey: edit → publish → graph delta parity.

A real TaskRunner run over a small project with the scripted backend: the
model edits a file through the tool loop, host checks pass, the changeset
publishes, and the publication hot path delta-syncs the structural graph —
the store must equal a clean build of the published sources, and the sync
must be recorded on the task. Scripted host mechanics; no live model.
"""
import json

from conftest import ScriptedBackend

from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.config import AtomicConfig
from atomic_flow.graph.project import clean_build, project_files
from atomic_flow.graph.sync import store_path
from atomic_flow.tree.builder import build_tree, save_tree


def test_publication_hot_path_delta_syncs_graph_to_parity(tmp_path, isolated_home):
    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_text("def sample():\n    return 1\n", encoding="utf-8")
    save_tree(project, build_tree(project))

    new_source = "def sample():\n    return 2\n"

    answerer_responses = iter([
        {"tool": "edit_node",
         "args": {"node_id": "sample.py::sample", "new_source": new_source}},
        {"text": "Done.", "confidence": "high"},
    ])

    def answerer(user_text):
        return next(answerer_responses)

    backend = ScriptedBackend({
        "parser": {"task_type": "modify", "focus_terms": ["sample"]},
        "answerer": answerer,
        "critic": {"verdict": "ok"},
    })
    config = AtomicConfig()
    config.task.max_replans = 0
    runner = TaskRunner(config, project, backend, approval_callback=lambda *a, **k: True)
    task = runner.run_plan("change sample", Plan(steps=[
        PlanStep(description="Change sample value", verify_with="py_compile")]))
    assert task.status == "done", task.error
    assert (project / "sample.py").read_text(encoding="utf-8") == new_source

    # Publication recorded the graph sync on the task.
    events = [e for e in task.budget_events if e.get("event") == "graph_sync"]
    assert events and not events[-1].get("failed") and not events[-1].get("fallback")

    # Delta-synced store equals a clean build of the published sources.
    document = json.loads(store_path(project).read_text(encoding="utf-8"))
    expected = clean_build(project_files(project))
    live_nodes = sorted((n["id"], sorted(n.get("facts", {}).items(), key=str)) for n in document["nodes"])
    clean_nodes = sorted((n["id"], sorted(n.get("facts", {}).items(), key=str)) for n in expected["nodes"])
    assert live_nodes == clean_nodes
    live_edges = sorted((e["kind"], e["src"], e["dst"]) for e in document["edges"])
    clean_edges = sorted((e["kind"], e["src"], e["dst"]) for e in expected["edges"])
    assert live_edges == clean_edges
