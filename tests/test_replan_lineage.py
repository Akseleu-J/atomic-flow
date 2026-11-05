"""P06-2: hierarchical replan lineage obligations fail closed."""
from atomic_flow.agents.replan_lineage import lineage_preserved

OBLIGATIONS = {
    "criteria": ["Derivative kind propagates", "No regression in core kinds"],
    "scope_paths": ["sympy/core/function.py"],
    "max_budget_usd": 1.8,
    "required_checks": {"py_compile"},
}


def test_inheriting_replan_that_keeps_obligations_passes():
    steps = [{"criteria": ["Derivative kind propagates"], "scope_paths": None, "checks": ["py_compile"]},
             {"criteria": ["No regression in core kinds"], "scope_paths": ["sympy/core/function.py"],
              "checks": ["py_compile"], "budget_usd": 0.5}]
    ok, reasons = lineage_preserved(OBLIGATIONS, steps)
    assert ok, reasons


def test_lost_criterion_blocks_replan():
    steps = [{"criteria": ["Derivative kind propagates"], "checks": ["py_compile"]}]
    ok, reasons = lineage_preserved(OBLIGATIONS, steps)
    assert not ok and any("criterion lost" in r for r in reasons)


def test_scope_widening_is_refused():
    steps = [{"criteria": ["Derivative kind propagates", "No regression in core kinds"],
              "scope_paths": ["sympy/core/function.py", "sympy/core/kind.py"],
              "checks": ["py_compile"]}]
    ok, reasons = lineage_preserved(OBLIGATIONS, steps)
    assert not ok and any("outside obligated scope" in r for r in reasons)


def test_step_budget_above_ancestor_cap_is_refused():
    steps = [{"criteria": ["Derivative kind propagates", "No regression in core kinds"],
              "checks": ["py_compile"], "budget_usd": 2.5}]
    ok, reasons = lineage_preserved(OBLIGATIONS, steps)
    assert not ok and any("exceeds ancestor cap" in r for r in reasons)


def test_missing_required_final_check_is_refused():
    steps = [{"criteria": ["Derivative kind propagates", "No regression in core kinds"],
              "scope_paths": ["sympy/core/function.py"], "checks": []}]
    ok, reasons = lineage_preserved(OBLIGATIONS, steps)
    assert not ok and any("required final checks" in r for r in reasons)


def test_malformed_input_fails_closed():
    ok, reasons = lineage_preserved(None, [])
    assert not ok and reasons
    ok, reasons = lineage_preserved({"criteria": []}, [])
    assert not ok
