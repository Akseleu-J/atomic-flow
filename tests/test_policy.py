"""Policy engine: declarative tool approval rules."""
from __future__ import annotations

from atomic_flow.agents.policy import PolicyEngine, READ_ONLY_TOOLS


def test_read_only_never_asks():
    engine = PolicyEngine()
    for tool in READ_ONLY_TOOLS:
        assert engine.decide(tool, {}, project_trusted=False).action == "auto"


def test_edit_in_trusted_small_is_auto():
    decision = PolicyEngine().decide("edit_node", {"new_source": "def f():\n    pass\n"}, project_trusted=True)
    assert decision.action == "auto"


def test_edit_in_trusted_large_asks():
    big = "def f():\n" + "\n".join(f"    x{i} = {i}" for i in range(25))
    assert PolicyEngine().decide("edit_node", {"new_source": big}, project_trusted=True).action == "ask"


def test_edit_in_untrusted_always_asks():
    decision = PolicyEngine().decide("edit_node", {"new_source": "def f(): pass"}, project_trusted=False)
    assert decision.action == "ask"


def test_run_command_forbidden_pattern():
    decision = PolicyEngine().decide("run_command", {"command": "sudo rm -rf /"}, project_trusted=True)
    assert decision.action == "forbid"


def test_run_command_allows_pytest():
    decision = PolicyEngine().decide("run_command", {"command": "pytest tests/"}, project_trusted=True)
    assert decision.action == "auto"


def test_custom_policy_overrides_default(tmp_path):
    policy_dir = tmp_path / ".atomic_flow"
    policy_dir.mkdir()
    (policy_dir / "policy.yaml").write_text(
        "version: 1\nrules:\n  - match: {tool: edit_node}\n    action: forbid\n", encoding="utf-8"
    )
    assert PolicyEngine.load(tmp_path).decide("edit_node", {}, project_trusted=True).action == "forbid"
