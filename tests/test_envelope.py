"""P06-1: replan envelope predicate and no-progress guard (pure functions)."""
from atomic_flow.agents.envelope import envelope_preserved, no_progress
from atomic_flow.agents.task import Step

APPROVAL = {
    "schema_version": 2,
    "plan": {"steps": [
        {"index": 0, "tools_needed": ["read_file", "edit_node"], "verify_with": "py_compile"},
        {"index": 1, "tools_needed": ["run_command"], "verify_with": "pytest"},
    ]},
    "internal_replan": {"version": 2, "deadline_at": 253402300799,
                        "allowed_tools": ["edit_node", "read_file", "run_command"],
                        "required_verify": "py_compile", "max_budget_usd": 0.05,
                        "max_model_calls": 100, "max_replans": 2, "runtime_digest": "a" * 64,
                        "replan_failures": []},
}


def test_in_scope_replan_is_preserved():
    steps = [Step(index=0, description="retry edit", tools_needed=["edit_node", "run_command"], verify_with="py_compile")]
    ok, reasons = envelope_preserved(APPROVAL, steps, failure="syntax check failed", budget_spent=0.01)
    assert ok, reasons


def test_widening_is_refused_with_reasons():
    network = [Step(index=0, description="install dep", tools_needed=["run_command", "shell_exec"],
                    verify_with="pytest")]
    ok, reasons = envelope_preserved(APPROVAL, network, failure="missing dependency", budget_spent=0.01)
    assert not ok and any("shell_exec" in r for r in reasons)


def test_mutating_tool_introduction_is_refused():
    read_only = {**APPROVAL, "internal_replan": {**APPROVAL["internal_replan"],
                                                "allowed_tools": ["read_file"], "required_verify": "none"}}
    steps = [Step(index=0, description="quick fix", tools_needed=["read_file", "patch_file"],
                  verify_with="none")]
    ok, reasons = envelope_preserved(read_only, steps, failure="", budget_spent=0.0)
    assert not ok and any("mutating tool" in r for r in reasons)


def test_new_verify_kind_is_refused():
    steps = [Step(index=0, description="try tests", tools_needed=["run_command"], verify_with="pytest")]
    read_only = {**APPROVAL, "internal_replan": {**APPROVAL["internal_replan"],
                                                "allowed_tools": ["read_file"], "required_verify": "py_compile"}}
    ok, reasons = envelope_preserved(read_only, steps, failure="", budget_spent=0.0)
    assert not ok and any("execution" in r for r in reasons)


def test_budget_cap_blocks_replan_continuation():
    steps = [Step(index=0, description="one more", tools_needed=["edit_node"], verify_with="py_compile")]
    ok, reasons = envelope_preserved(APPROVAL, steps, failure="x", budget_spent=0.06)
    assert not ok and any("budget" in r for r in reasons)


def test_no_progress_guard_trips_on_recurring_signature():
    approval = {**APPROVAL, "internal_replan": {**APPROVAL["internal_replan"],
                "replan_failures": ["edit rolled back: py_compile failed", "edit rolled back: py_compile failed"]}}
    assert no_progress(approval, "edit rolled back: py_compile failed\nextra line")
    assert not no_progress(approval, "a different failure entirely")
    steps = [Step(index=0, description="same again", tools_needed=["edit_node"], verify_with="py_compile")]
    ok, reasons = envelope_preserved(approval, steps, failure="edit rolled back: py_compile failed",
                                     budget_spent=0.01)
    assert not ok and any("no-progress" in r for r in reasons)


def test_legacy_missing_envelope_and_nonfinite_accounting_fail_closed():
    for approval in ({"schema_version": 1}, {"schema_version": 2}, None):
        assert not envelope_preserved(approval, [Step(0, "retry")], failure="", budget_spent=0)[0]
    for spent in (float("nan"), float("inf"), -1, True):
        assert not envelope_preserved(APPROVAL, [Step(0, "retry")], failure="", budget_spent=spent)[0]


def test_whitespace_failure_is_not_a_crash():
    assert not no_progress(APPROVAL, " \n\t ")


def test_replan_cannot_discard_edit_or_execution_obligations():
    read_only = [Step(0, "only read", tools_needed=["read_file"], verify_with="py_compile")]
    ok, reasons = envelope_preserved(APPROVAL, read_only, failure="", budget_spent=0)
    assert not ok and any("edit obligation" in r for r in reasons)
    assert any("execution/check obligation" in r for r in reasons)
