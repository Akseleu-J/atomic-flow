"""Initial planning observes current source/budgets without gaining read authority."""
import json

from test_repair_budget import Responses

from atomic_flow.agents.changeset import _files
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.step_evidence import manifest_digest
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.backends.base import ChatResult
from atomic_flow.config import AtomicConfig


def test_initial_plan_receives_current_revision_budget_and_empty_effect_history(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "source.py").write_text("VALUE = 2\n", encoding="utf-8")

    class Backend(Responses):
        def chat(self, messages, **kwargs):
            self.calls.append(messages)
            return ChatResult(Plan(steps=[PlanStep(description="Report the source value", verify_with="none")]).model_dump_json(),
                              tokens_in=10, tokens_out=5, cost_usd=0.0005)

    config = AtomicConfig()
    config.task.max_model_calls = 7
    config.task.max_budget_usd = 0.5
    backend = Backend()
    task = TaskRunner(config, project, backend).prepare("Report current value", candidates=[], n_files=1)
    assert task.status == "paused" and not task.plan_approval
    prompt = backend.calls[0][1]["content"]
    context = json.JSONDecoder().raw_decode(prompt.split("HOST PLANNING OBSERVATIONS", 1)[1].split("\n", 1)[1])[0]
    assert context["workspace_revision"] == manifest_digest(_files(project))
    assert context["graph_revision"] is None and context["graph_status"] == "advisory_not_read_proof"
    assert context["remaining_model_calls"] == 7 and context["remaining_budget_usd"] == 0.5
    assert context["completed_effects"] == context["known_failures"] == []
    assert not task.steps[0].evidence and task.budget_spent == 0.0005
