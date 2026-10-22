from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
import yaml
from pydantic import ValidationError

from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.loop import execute_tool
from atomic_flow.agents.policy import PolicyEngine, Rule
from atomic_flow.agents.source import digest
from atomic_flow.agents.tools import default_registry
from atomic_flow.agents.tools.find_files import FindFilesArgs, FindFilesTool, glob_matches
from atomic_flow.agents.tools.read_file import ReadFileArgs, ReadFileTool
from atomic_flow.cli import main
from atomic_flow.config import AtomicConfig, explain_config, load_config, trust_project, user_dir
from atomic_flow.diagnostics import offline_diagnostics
from atomic_flow.tree.builder import build_tree
from atomic_flow.tree.node import TreeNode


def context(root):
    return ToolContext(root, {n["node_id"]: TreeNode.model_validate(n) for n in build_tree(root)["nodes"]},
                       enforce_read_before_edit=True)


@pytest.mark.parametrize("path,pattern,expected", [
    ("a.py", "**/*.py", True), ("src/a.py", "**/*.py", True),
    ("src/sub/a.py", "src/*.py", False), ("src/a.py", "src/?.py", True),
    ("src/sub/a.py", "src/**/a.py", True), ("a.md", "**/*.py", False),
])
def test_glob_whole_path_semantics(path, pattern, expected):
    assert glob_matches(path, pattern) == expected


def test_file_discovery_pagination_ignores_and_policy(tmp_path):
    for name in ("a.py", "b.py", "c.py", ".env.py", "credentials.py"):
        (tmp_path / name).write_text("VALUE = 1\n")
    ctx = context(tmp_path)
    tool = FindFilesTool()
    first = tool.run(FindFilesArgs(pattern="**/*.py", limit=2), ctx)
    second = tool.run(FindFilesArgs(pattern="**/*.py", offset=first["next_offset"]), ctx)
    assert [x["path"] for x in first["files"] + second["files"]] == ["a.py", "b.py", "c.py"]
    assert not ctx.reads
    assert PolicyEngine().decide("find_files", {}, project_trusted=False).action == "auto"
    result, record = execute_tool({"tool": "find_files", "args": {}}, default_registry(), ctx, allowed_tools=[])
    assert result["outcome"] == "denied" and record["result_summary"]["outcome"] == "denied"


@pytest.mark.parametrize("pattern", ["../*", "/tmp/*", "C:/*", "a\\*", "a//b"])
def test_glob_rejects_escaping_patterns(pattern):
    with pytest.raises(ValidationError):
        FindFilesArgs(pattern=pattern)


def test_line_reads_accumulate_actual_file_coverage_and_reject_stale_hash(tmp_path):
    original = "FIRST = 1\n" + "# Ж" * 300 + "\nLAST = 2\n"
    path = tmp_path / "code.py"
    path.write_bytes(original.encode())
    ctx = context(tmp_path)
    tool = ReadFileTool()
    partial = tool.run(ReadFileArgs(path="code.py", start_line=2, end_line=2, limit=256), ctx)
    assert partial["truncated"] and not partial["read_complete"]
    with pytest.raises(OSError, match="complete"):
        ctx.require_read("code.py", digest(original.encode()))
    offset = partial["next_offset"]
    while offset is not None:
        page = tool.run(ReadFileArgs(path="code.py", start_line=2, end_line=2, offset=offset, limit=256), ctx)
        offset = page["next_offset"]
    tool.run(ReadFileArgs(path="code.py", start_line=1, end_line=1), ctx)
    final = tool.run(ReadFileArgs(path="code.py", start_line=3), ctx)
    assert final["read_complete"]
    path.write_text("CHANGED = 9\n")
    assert "error" in tool.run(ReadFileArgs(path="code.py", start_line=1), ctx)


def test_line_ranges_keep_redaction_and_empty_out_of_range_is_not_full_read(tmp_path):
    (tmp_path / "a.py").write_text('api_key = "sk-abcdefghijklmnopqrstuvwxyz0123456789"\nB = 2\n')
    ctx = context(tmp_path)
    result = ReadFileTool().run(ReadFileArgs(path="a.py", start_line=1, end_line=1), ctx)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in result["source"]
    assert not result["read_complete"]
    result = ReadFileTool().run(ReadFileArgs(path="a.py", start_line=500), ctx)
    assert result["source"] == "" and not result["read_complete"]
    with pytest.raises(ValidationError):
        ReadFileArgs(path="a.py", start_line=4, end_line=3)


def test_policy_explanation_matches_decision_without_executing_and_lints_conflict():
    engine = PolicyEngine(user_rules=[Rule("read_file", "auto"), Rule("read_file", "auto")],
                          session_rules=[Rule("read_file", "forbid")])
    trace = engine.explain("read_file", {"path": "a.py"}, project_trusted=True)
    assert trace["action"] == "forbid" and trace["source"] == "session"
    assert sum(row["selected"] for row in trace["matches"]) == 1
    codes = {item["code"] for item in engine.lint()}
    assert codes >= {"duplicate_or_redundant", "deny_overrides_allow"}


def test_effective_config_uses_runtime_resolver_and_masks_credentials(tmp_path, monkeypatch):
    user_dir().mkdir(parents=True)
    (user_dir() / "config.yaml").write_text(yaml.safe_dump({"mode": "simple", "backends": {
        "openai-compatible": {"api_key": "private-user-key"}}}))
    (tmp_path / ".atomic_flow").mkdir()
    (tmp_path / ".atomic_flow/config.yaml").write_text(yaml.safe_dump({
        "mode": "hard", "execution": {"mode": "docker"}, "backends": {"openai-compatible": {"api_key": "evil-key"}}}))
    monkeypatch.setenv("ATOMIC_FLOW_MODEL", "selected-model")
    explained = explain_config(tmp_path)
    assert explained["effective"]["mode"] == load_config(tmp_path).mode == "hard"
    assert explained["sources"]["mode"] == "project"
    assert explained["sources"]["backends.openai-compatible.model"] == "environment:ATOMIC_FLOW_MODEL"
    assert set(explained["ignored_untrusted_project_keys"]) == {"backends", "execution"}
    assert "private-user-key" not in json.dumps(explained) and "evil-key" not in json.dumps(explained)
    trust_project(tmp_path)
    assert explain_config(tmp_path)["sources"]["backends.openai-compatible.api_key"] == "project"


def test_offline_doctor_detects_unsettled_hold_and_corrupt_task(tmp_path):
    state = user_dir()
    (state / "ledger").mkdir(parents=True)
    ledger = state / "ledger" / f"{datetime.now(UTC).date().isoformat()}.jsonl"
    ledger.write_text(json.dumps({"call_id": "model_1", "status": "in_flight", "reserved_usd": 1}) + "\n")
    (state / "tasks").mkdir()
    (state / "tasks/task_123456789abc.json").write_text("{")
    report = offline_diagnostics(tmp_path, AtomicConfig())
    assert report["model_calls"] == 0 and report["offline"] and not report["healthy"]
    checks = {c["check"]: c for c in report["checks"]}
    assert checks["spend_holds"]["unresolved"][0]["call_id"] == "model_1"
    assert checks["durable_state"]["status"] == "error"
    assert not checks["execution"]["daemon_and_image_verified"]


def test_offline_cli_routes_work_without_backend(tmp_path, capsys):
    assert main(["policy", "explain", "read_file", "--project", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "auto"
    assert main(["config", "explain", "--project", str(tmp_path)]) == 0
    assert "sources" in json.loads(capsys.readouterr().out)
    assert main(["doctor", "--json", "--project", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["model_calls"] == 0
