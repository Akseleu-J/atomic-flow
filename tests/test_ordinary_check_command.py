"""The ordinary-check spelling preserves host contracts and pytest restrictions."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.policy import PolicyEngine, Rule
from atomic_flow.agents.tools.run_command import RunCommandArgs, RunCommandTool


@pytest.mark.parametrize("extra", [{"runner": "pytest"}, {"image": "other"}, {"env": {"KEY": "value"}}])
def test_model_cannot_select_runner_image_or_environment(extra):
    with pytest.raises(ValidationError):
        RunCommandArgs(command="check", **extra)


def test_check_without_host_obligation_never_falls_back(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unbound ordinary check must not execute")
    monkeypatch.setattr("atomic_flow.agents.tools.run_command.run_python", forbidden)
    result = RunCommandTool().run(RunCommandArgs(command="check"), ToolContext(tmp_path, {}))
    assert result["command"] == "check"
    assert "requires a host" in result["error"]


@pytest.mark.parametrize("runner,command,allowed", [
    ("pytest", "check", True), ("pytest", "pytest", True),
    ("sympy-native", "check", True), ("sympy-native", "pytest", False),
])
def test_command_routes_only_to_complete_host_obligation(tmp_path, monkeypatch, runner, command, allowed):
    from atomic_flow.check_obligation import CheckObligation
    from atomic_flow.check_spec import CheckSpec

    path = "sympy/core/tests/test_value.py" if runner == "sympy-native" else "tests/test_value.py"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"def test_value():\n    assert True\n")
    from atomic_flow.agents.verify import check_source_revision
    spec = CheckSpec("example", "a" * 40, "b" * 40, check_source_revision(tmp_path),
                     "example/checks@sha256:" + "c" * 64, "/usr/local/bin/python", runner, (path,))
    bound = CheckObligation.seal(spec, tmp_path, environment_digest="d" * 64, deadline_at=400, now=lambda: 100)
    calls = []
    def execute(obligation, workspace, **kwargs):
        assert obligation is bound and workspace == tmp_path
        calls.append(obligation)
        result = SimpleNamespace(returncode=0, timed_out=False, stdout="native output", stderr="")
        return spec, SimpleNamespace(result=result, check_passed=True)
    monkeypatch.setattr("atomic_flow.check_obligation.run_obligation_check", execute)
    monkeypatch.setattr("atomic_flow.check_obligation.check_observation",
                        lambda obligation, actual_spec, receipt: {"check_runner": actual_spec.runner})
    context = ToolContext(tmp_path, {}, private_workspace=True, check_obligation=bound)
    result = RunCommandTool().run(RunCommandArgs(command=command), context)
    assert result["command"] == command
    if allowed:
        assert result["exit_code"] == 0 and result["check_runner"] == runner and len(calls) == 1
    else:
        assert "requires command check" in result["error"] and not calls
    narrowed = RunCommandTool().run(RunCommandArgs(command="check", target=path), context)
    assert "cannot be narrowed" in narrowed["error"]
    assert len(calls) == int(allowed)


@pytest.mark.parametrize("action", ["ask", "forbid"])
@pytest.mark.parametrize("layer", ["host", "user", "project", "session"])
@pytest.mark.parametrize("command,restricted", [("check", "pytest"), ("pytest", "check")])
def test_equivalent_spellings_cannot_bypass_restrictions(action, layer, command, restricted):
    rules = [Rule("run_command", "auto", command_matches="^" + command + "$"),
             Rule("run_command", action, command_matches="^" + restricted + "$")]
    engine = PolicyEngine(**{layer + "_rules": rules})
    decision = engine.decide("run_command", {"command": command}, project_trusted=True)
    explanation = engine.explain("run_command", {"command": command}, project_trusted=True)
    assert decision.action == action and decision.source == layer
    assert explanation["action"] == action and explanation["source"] == layer
    selected = [match for match in explanation["matches"] if match["selected"]]
    assert len(selected) == 1 and selected[0]["command"] == restricted


def test_default_check_is_auto_but_unrelated_commands_keep_approval():
    engine = PolicyEngine()
    assert engine.decide("run_command", {"command": "check"}, project_trusted=False).action == "auto"
    assert engine.decide("run_command", {"command": "unchecked"}, project_trusted=True).action == "ask"
    assert engine.decide("run_command", {"command": "check_extra"}, project_trusted=True).action == "ask"


def test_explicit_check_restriction_survives_pytest_allow():
    engine = PolicyEngine(user_rules=[Rule("run_command", "forbid", command_matches=r"^check$"),
                                      Rule("run_command", "auto", command_matches=r"^pytest$")])
    assert engine.decide("run_command", {"command": "check"}, project_trusted=True).action == "forbid"


@pytest.mark.parametrize("command", [[], {}, ["check"], {"command": "check"}])
def test_offline_policy_handles_malformed_command_without_type_error(command):
    engine = PolicyEngine()
    assert engine.decide("run_command", {"command": command}, project_trusted=True).action == "ask"
    assert engine.explain("run_command", {"command": command}, project_trusted=True)["action"] == "ask"
