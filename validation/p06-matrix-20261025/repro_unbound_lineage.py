"""Expected failure: emulate pre-fix namespace handling in this process only."""
import pytest
from atomic_flow.agents import task_loop

task_loop.inherit_constraints = lambda task, goals, steps: (goals, [])
raise SystemExit(pytest.main([
    "tests/test_p06_constraints.py::test_original_goal_cap_survives_replan_and_restart",
    "-k", "calls and True", "-q",
    "--junitxml=validation/p06-matrix-20261025/unbound-lineage-red.xml",
]))
