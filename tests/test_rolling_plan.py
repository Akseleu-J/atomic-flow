"""Expansion proposals preserve the reviewed graph and never execute effects."""
from copy import deepcopy

import pytest

from atomic_flow.agents.goals import Goal
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.rolling_plan import propose_expansion
from atomic_flow.agents.task import Step, Task, validate_dependencies
from atomic_flow.state import StateError


def task():
    return Task("task_123456789abc", "Keep the approved objective", ".", [
        Step(0, "Read prerequisite", status="done", result_summary="already read", request_id="read-0"),
        Step(4, "Implement the reviewed change", tools_needed=["edit_node"], verify_with="pytest",
             depends_on=[0], parent_goal="p0:fix", acceptance_criteria=["Regression passes"]),
        Step(9, "Publish the result", depends_on=[4]),
        Step(12, "Unrelated failed work", status="failed", result_summary="retained failure"),
    ], plan_approval={"digest": "prior-review"},
        goals=[Goal("p0:fix", "Repair the approved behavior", acceptance_criteria=["No regressions"])])


def plan(*dependencies):
    return Plan(steps=[PlanStep(description=f"Concrete implementation part {i}", tools_needed=["edit_node"],
                               verify_with="pytest", depends_on=deps)
                       for i, deps in enumerate(dependencies or (None, None))])


def test_expansion_preserves_history_ids_requirements_and_clears_approval():
    original = task()
    before = deepcopy(original)
    result = propose_expansion(original, 4, plan())
    assert original == before
    assert [step.index for step in result.steps] == [0, 4, 9, 12, 13, 14]
    assert result.steps[0] == original.steps[0]
    assert result.steps[3] == original.steps[3]
    assert result.steps[1].status == "superseded"
    assert result.steps[2].depends_on == [14]
    assert result.steps[4].depends_on == [0]
    assert result.steps[5].depends_on == [13]
    for child in result.steps[4:]:
        assert child.parent_goal == "p0:fix"
        assert child.acceptance_criteria == ["Regression passes"]
        assert child.status == "pending"
        assert not child.request_id
    assert result.query == original.query
    assert result.goals == original.goals
    assert result.expansion_count == 1
    assert result.status == "paused"
    assert result.plan_approval == {}
    result.steps[-1].acceptance_criteria.append("No alias")
    assert original == before


def test_parallel_children_join_all_terminals_and_preserve_other_prerequisites():
    original = task()
    original.steps[2].depends_on = [0, 4]
    result = propose_expansion(original, 4, plan([], [], [0]))
    assert [s.depends_on for s in result.steps[4:]] == [[0], [0], [13]]
    assert result.steps[2].depends_on == [0, 14, 15]
    validate_dependencies(result.steps)


def test_forward_local_edge_is_supported_without_reordering_stable_ids():
    result = propose_expansion(task(), 4, plan([1], []))
    assert result.steps[4].depends_on == [14]
    assert result.steps[5].depends_on == [0]
    assert result.steps[2].depends_on == [13]


@pytest.mark.parametrize("deps", [([1], [0]), ([7], []), ([0], [])])
def test_unvalidated_plan_cannot_smuggle_cycles_missing_or_self_edges(deps):
    original = task()
    before = deepcopy(original)
    invalid = Plan.model_construct(steps=[PlanStep.model_construct(description="Invalid graph", depends_on=d,
                       tools_needed=["edit_node"], verify_with="pytest") for d in deps], goals=[], rationale="")
    with pytest.raises((ValueError, StateError)):
        propose_expansion(original, 4, invalid)
    assert original == before


@pytest.mark.parametrize("change", [
    {"verify_with": "none"}, {"verify_with": "py_compile"},
    {"tools_needed": []}, {"tools_needed": ["edit_node", "run_shell"]},
    {"acceptance_criteria": ["Weaker requirement"]},
])
def test_expansion_refuses_changed_obligations_without_mutation(change):
    original = task()
    before = deepcopy(original)
    proposal = plan()
    proposal.steps[0] = proposal.steps[0].model_copy(update=change)
    with pytest.raises(ValueError):
        propose_expansion(original, 4, proposal)
    assert original == before


@pytest.mark.parametrize("status", ["done", "failed", "running", "blocked", "superseded"])
def test_only_pending_placeholder_may_be_expanded(status):
    original = task()
    original.steps[1].status = status
    with pytest.raises(ValueError, match="pending"):
        propose_expansion(original, 4, plan())


def test_pending_placeholder_with_prior_request_is_not_replayed():
    original = task()
    original.steps[1].request_id = "uncertain-effect"
    with pytest.raises(ValueError, match="unexecuted"):
        propose_expansion(original, 4, plan())


def test_expansion_does_not_rewrite_executed_dependents():
    original = task()
    original.steps[2].status = "done"
    with pytest.raises(ValueError, match="executed work"):
        propose_expansion(original, 4, plan())


def test_missing_placeholder_and_invalid_existing_graph_are_refused():
    original = task()
    with pytest.raises(ValueError, match="missing"):
        propose_expansion(original, 55, plan())
    original.steps[2].depends_on = [999]
    with pytest.raises(StateError, match="missing"):
        propose_expansion(original, 4, plan())


def test_history_and_expansion_count_limits_include_superseded_steps():
    original = task()
    original.expansion_count = 8
    with pytest.raises(ValueError, match="limit"):
        propose_expansion(original, 4, plan())
    original.expansion_count = 0
    original.steps += [Step(i, "Historical work", status="superseded") for i in range(20, 143)]
    assert len(original.steps) == 127
    with pytest.raises(ValueError, match="128"):
        propose_expansion(original, 4, plan())


def test_more_than_three_children_and_goal_rewrite_are_rejected():
    with pytest.raises(ValueError, match="1–3"):
        propose_expansion(task(), 4, plan([], [], [], []))
    rewrite = Plan.model_construct(steps=plan().steps, goals=[{"id": "other"}], rationale="")
    with pytest.raises(ValueError):
        propose_expansion(task(), 4, rewrite)


def test_second_expansion_keeps_first_children_and_ids_stable():
    first = propose_expansion(task(), 4, plan())
    second = propose_expansion(first, 13, plan())
    assert second.expansion_count == 2
    assert second.steps[4].status == "superseded"
    assert second.steps[5].index == 14
    assert second.steps[5].depends_on == [16]
    assert [s.index for s in second.steps[-2:]] == [15, 16]
    assert second.steps[-2].depends_on == [0]
    assert first.steps[4].status == "pending"


def test_children_narrow_scope_inherit_caps_and_merge_only_advisory_links():
    original = task()
    original.steps[1].scope_paths = ["src/a.py", "tests/test_a.py"]
    original.steps[1].linked_code_nodes = ["src/a.py::fix"]
    original.steps[1].linked_test_nodes = ["tests/test_a.py::test_fix"]
    original.steps[1].evidence = {"obsolete": "old snapshot"}
    original.goals[0].max_model_calls = 5
    original.goals[0].max_budget_usd = 0.05
    proposal = plan()
    proposal.steps[0].scope_paths = ["src/a.py"]
    proposal.steps[0].linked_code_nodes = ["src/a.py::fix", "src/a.py::helper"]
    result = propose_expansion(original, 4, proposal)
    assert result.steps[4].scope_paths == ["src/a.py"]
    assert result.steps[5].scope_paths == ["src/a.py", "tests/test_a.py"]
    assert result.steps[4].linked_code_nodes == ["src/a.py::fix", "src/a.py::helper"]
    assert result.steps[4].linked_test_nodes == ["tests/test_a.py::test_fix"]
    assert result.steps[4].evidence == {}
    assert result.steps[5].evidence == {}
    assert result.goals == original.goals
    assert all(child.parent_goal == original.goals[0].id for child in result.steps[4:])
    assert original.steps[1].evidence == {"obsolete": "old snapshot"}


@pytest.mark.parametrize("existing", [[], ["src/a.py"]])
def test_explicit_scope_cannot_widen_inherited_or_exact_scope(existing):
    original = task()
    original.steps[1].scope_paths = existing
    proposal = plan()
    proposal.steps[0].scope_paths = ["src/b.py"]
    before = deepcopy(original)
    with pytest.raises(ValueError, match="scope"):
        propose_expansion(original, 4, proposal)
    assert original == before


def test_link_union_is_bounded_without_discarding_original_links():
    original = task()
    original.steps[1].linked_code_nodes = [f"src/a.py::symbol_{i}" for i in range(64)]
    proposal = plan()
    proposal.steps[0].linked_code_nodes = ["src/a.py::extra"]
    before = deepcopy(original)
    with pytest.raises(ValueError, match="64"):
        propose_expansion(original, 4, proposal)
    assert original == before
