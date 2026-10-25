"""P06-1: replan envelope predicate and no-progress guard (pure functions)."""
from atomic_flow.agents.envelope import envelope_preserved, no_progress
from atomic_flow.agents.task import Step

APPROVAL = {
    "schema_version": 1,
    "plan": {"steps": [
        {"index": 0, "tools_needed": ["read_file", "edit_node"], "verify_with": "py_compile"},
        {"index": 1, "tools_needed": ["run_command"], "verify_with": "pytest"},
    ]},
    "limits": {"max_budget_usd": 0.05},
    "replan_failures": [],
}


def test_in_scope_replan_is_preserved():
    steps = [Step(index=0, description="retry edit", tools_needed=["edit_node"], verify_with="py_compile")]
    ok, reasons = envelope_preserved(APPROVAL, steps, failure="syntax check failed", budget_spent=0.01)
    assert ok, reasons


def test_widening_is_refused_with_reasons():
    network = [Step(index=0, description="install dep", tools_needed=["run_command", "shell_exec"],
                    verify_with="pytest")]
    ok, reasons = envelope_preserved(APPROVAL, network, failure="missing dependency", budget_spent=0.01)
    assert not ok and any("shell_exec" in r for r in reasons)


def test_mutating_tool_introduction_is_refused():
    read_only = {"schema_version": 1,
                 "plan": {"steps": [{"index": 0, "tools_needed": ["read_file"], "verify_with": "none"}]},
                 "limits": {}, "replan_failures": []}
    steps = [Step(index=0, description="quick fix", tools_needed=["read_file", "patch_file"],
                  verify_with="none")]
    ok, reasons = envelope_preserved(read_only, steps, failure="", budget_spent=0.0)
    assert not ok and any("mutating tool" in r for r in reasons)


def test_new_verify_kind_is_refused():
    steps = [Step(index=0, description="try tests", tools_needed=["run_command"], verify_with="pytest")]
    read_only = {"schema_version": 1,
                 "plan": {"steps": [{"index": 0, "tools_needed": ["run_command"], "verify_with": "py_compile"}]},
                 "limits": {}, "replan_failures": []}
    ok, reasons = envelope_preserved(read_only, steps, failure="", budget_spent=0.0)
    assert not ok and any("verify_with" in r for r in reasons)


def test_budget_cap_blocks_replan_continuation():
    steps = [Step(index=0, description="one more", tools_needed=["edit_node"], verify_with="py_compile")]
    ok, reasons = envelope_preserved(APPROVAL, steps, failure="x", budget_spent=0.06)
    assert not ok and any("budget" in r for r in reasons)


def test_no_progress_guard_trips_on_recurring_signature():
    approval = {**APPROVAL, "replan_failures": ["edit rolled back: py_compile failed",
                                                "edit rolled back: py_compile failed"]}
    assert no_progress(approval, "edit rolled back: py_compile failed\nextra line")
    assert not no_progress(approval, "a different failure entirely")
    steps = [Step(index=0, description="same again", tools_needed=["edit_node"], verify_with="py_compile")]
    ok, reasons = envelope_preserved(approval, steps, failure="edit rolled back: py_compile failed",
                                     budget_spent=0.01)
    assert not ok and any("no-progress" in r for r in reasons)
