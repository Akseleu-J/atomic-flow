from pathlib import Path

from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.tools import ROLE_TOOLS, default_registry
from atomic_flow.agents.tools.run_command import RunCommandArgs, RunCommandTool


def test_run_command_is_registered_for_answerer_and_critic():
    registry = default_registry()
    assert registry.get("run_command") is not None
    assert "run_command" in ROLE_TOOLS["answerer"]
    assert "run_command" in ROLE_TOOLS["critic"]


def test_run_command_runs_pytest_inside_project(tmp_path: Path):
    (tmp_path / "test_smoke.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = RunCommandTool().run(RunCommandArgs(command="pytest", target="test_smoke.py"), ToolContext(tmp_path, {}))
    assert result["exit_code"] == 0
    assert "1 passed" in result["stdout"]


def test_run_command_rejects_target_escape(tmp_path: Path):
    result = RunCommandTool().run(RunCommandArgs(command="pytest", target="../outside"), ToolContext(tmp_path, {}))
    assert result == {"error": "target escapes project root"}
