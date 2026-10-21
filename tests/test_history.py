import json

import pytest
from conftest import ScriptedBackend

from atomic_flow.config import AtomicConfig, ConversationConfig, load_config, user_dir
from atomic_flow.errors import ContextLimitError, RepeatedToolCall
from atomic_flow.llm.history import RequestHistory, wire_bytes
from atomic_flow.llm.orchestrator import Orchestrator
from atomic_flow.tree.builder import build_tree, save_tree


def messages():
    return [{"role": "system", "content": "Preserve permissions and task rules."},
            {"role": "user", "content": "Find sample without changing files."}]


def add(history, transcript, source, **extra):
    transcript.append({"role": "assistant", "content": '{"tool":"read_node","args":{"node_id":"n1"}}'})
    history.append_result(transcript, {"source": source, "outcome": "success", "node_id": "n1",
                                       "content_hash": "revision1", **extra})


def test_old_tool_bodies_compact_but_instructions_recent_data_and_proof_stay():
    transcript = messages()
    pinned = [dict(m) for m in transcript]
    history = RequestHistory(ConversationConfig(max_request_bytes=6000, recent_tool_results=1))
    add(history, transcript, "old-source-" * 450, next_offset=4500, offset=0)
    snapshot = history.prepare(transcript)
    add(history, transcript, "recent-source-" * 250)
    bounded = history.prepare(transcript)
    assert wire_bytes(bounded) <= 6000
    assert bounded[:2] == pinned
    assert "old-source-" not in bounded[3]["content"]
    proof = json.loads(bounded[3]["content"].removeprefix("<tool_result>").removesuffix("</tool_result>"))
    assert proof["content_hash"] == "revision1" and proof["next_offset"] == 4500
    assert proof["history_body_omitted"] and proof["outcome"] == "success"
    assert "recent-source-" in bounded[-1]["content"]
    assert "old-source-" in snapshot[3]["content"]
    assert history.stats()["compacted_tool_results"] == 1


@pytest.mark.parametrize("payload", [
    {"outcome": "denied", "error": "user refused", "verification": {}},
    {"outcome": "error", "status": "rolled_back", "reason": "pytest failed", "checks": ["syntax", "pytest"]},
    {"outcome": "success", "status": "applied", "edit_id": "edit_123", "checks": ["syntax"], "content_hash": "new"},
])
def test_compaction_keeps_denials_errors_and_edit_proof(payload):
    transcript = messages()
    history = RequestHistory(ConversationConfig(max_request_bytes=5000, recent_tool_results=1))
    transcript.append({"role": "assistant", "content": "call"})
    history.append_result(transcript, {**payload, "diagnostic": "x" * 4000})
    add(history, transcript, "y" * 2000)
    bounded = history.prepare(transcript)
    compact = bounded[3]["content"]
    assert payload["outcome"] in compact
    for key in ("error", "reason", "edit_id", "content_hash"):
        if key in payload:
            assert payload[key] in compact
    assert "history_body_omitted" in compact


def test_oversized_pinned_or_current_message_is_not_silently_cut():
    history = RequestHistory(ConversationConfig(max_request_bytes=4096))
    transcript = messages()
    transcript[0]["content"] = "rule" * 2000
    with pytest.raises(ContextLimitError):
        history.prepare(transcript)
    assert transcript[0]["content"] == "rule" * 2000
    transcript = messages()
    add(history, transcript, "source" * 2000)
    with pytest.raises(ContextLimitError):
        history.prepare(transcript)
    assert "source" * 2000 in transcript[-1]["content"]


def test_repeat_guard_canonicalizes_arguments_and_resets_for_new_revision():
    history = RequestHistory(ConversationConfig(max_same_tool_call=2))
    call = {"tool": "read_node", "args": {"node_id": "n1", "offset": 0}}
    history.check_tool(call, 0)
    history.check_tool({"args": {"offset": 0, "node_id": "n1"}, "tool": "read_node"}, 0)
    with pytest.raises(RepeatedToolCall):
        history.check_tool(call, 0)
    history.check_tool(call, 1)
    history.check_tool({"tool": "read_node", "args": {"node_id": "n1", "offset": 100}}, 0)


def test_real_orchestrator_stops_repeated_tool_execution_and_keeps_audit(tmp_path):
    (tmp_path / "sample.py").write_text("def sample():\n    return 1\n")
    save_tree(tmp_path, build_tree(tmp_path))
    backend = ScriptedBackend({"answerer": {"tool": "read_node", "args": {"node_id": "sample.py::sample", "zoom": 2}}})
    cfg = AtomicConfig()
    cfg.conversation.max_same_tool_call = 2
    with pytest.raises(RepeatedToolCall):
        Orchestrator(cfg, tmp_path, backend).answer("sample", mode="simple", request_id="repeat_test")
    assert backend.roles() == ["answerer"] * 3
    artifact = user_dir() / "runs/repeat_test"
    calls = list(artifact.glob("*_tool_*.json"))
    assert len(calls) == 2
    assert json.loads((artifact / "error.json").read_text())["type"] == "RepeatedToolCall"


def test_real_orchestrator_refuses_large_request_before_any_backend_call(tmp_path):
    (tmp_path / "sample.py").write_text("def sample():\n    return 1\n")
    save_tree(tmp_path, build_tree(tmp_path))
    cfg = AtomicConfig()
    cfg.conversation.max_request_bytes = 4096
    backend = ScriptedBackend({"answerer": {"text": "unexpected"}})
    with pytest.raises(ContextLimitError):
        Orchestrator(cfg, tmp_path, backend).answer("large query " * 3000, mode="simple")
    assert not backend.calls


def test_untrusted_project_cannot_disable_request_limits(tmp_path):
    (tmp_path / ".atomic_flow").mkdir()
    (tmp_path / ".atomic_flow/config.yaml").write_text("conversation:\n  max_same_tool_call: 12\n  max_request_bytes: 2000000\n")
    cfg = load_config(tmp_path)
    assert cfg.conversation.max_same_tool_call == 3
    assert cfg.conversation.max_request_bytes == 131072
