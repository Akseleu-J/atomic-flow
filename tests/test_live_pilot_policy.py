"""Diagnostic profile accepts P05 research without accepting unchecked writes/execution."""
import importlib.util
import sys
from pathlib import Path

import pytest

from atomic_flow.agents.task import Step

SPEC = importlib.util.spec_from_file_location("live_pilot_policy", Path(__file__).resolve().parents[1] / "tools/evaluation_live_pilot.py")
pilot = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(Path(SPEC.origin).parent))
try:
    SPEC.loader.exec_module(pilot)
finally:
    sys.path.pop(0)


def test_p05_read_then_verified_edit_admitted_only_in_explicit_diagnostic_profile():
    steps = [Step(0, "Inspect the source", tools_needed=["read_file"], verify_with="none"),
             Step(1, "Change the property", tools_needed=["patch_file"], verify_with="py_compile", depends_on=[0])]
    assert pilot.plan_policy_violations(steps)
    assert pilot.plan_policy_violations(steps, allow_research=True) == []


@pytest.mark.parametrize("bad", [
    Step(0, "Unchecked write", tools_needed=["patch_file"], verify_with="none"),
    Step(0, "Run a command", tools_needed=["run_command"], verify_with="none"),
    Step(0, "Execute pytest", tools_needed=["read_file"], verify_with="pytest"),
    Step(0, "Claim a report", tools_needed=[], verify_with="none"),
])
def test_research_never_authorizes_unverified_effects_or_execution(bad):
    edit = Step(1, "Verified edit", tools_needed=["edit_node"], verify_with="py_compile")
    assert pilot.plan_policy_violations([bad, edit], allow_research=True)


def test_read_only_plan_cannot_replace_edit_task():
    assert pilot.plan_policy_violations([Step(0, "Research", tools_needed=["read_file"], verify_with="none")], allow_research=True)


def test_original_bounds_remain_unchanged_and_diagnostic_is_bounded():
    assert pilot.parser().parse_args(["--arm", "atomic", "--mirror", ".", "--output", "out"]).profile == "original"
    assert pilot.PROFILES["original"] == {"calls": 6, "input": 240_000, "wall": 600, "cost": .05}
    assert pilot.PROFILES["p05-diagnostic"]["calls"] == 20
    assert pilot.PROFILES["p05-diagnostic"]["cost"] == 1.8
