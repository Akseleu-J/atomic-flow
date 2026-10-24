"""State compatibility and crash-safe observation tests; never replay effects."""
import json
import subprocess
import sys
from dataclasses import asdict

import pytest

from atomic_flow.agents.task import Step, Task, load, save, tasks_dir
from atomic_flow.sessions import MAX_EVENT_BYTES, SessionJournal, list_sessions
from atomic_flow.state import StateError


def test_legacy_task_load_migrates_without_rewriting_and_roundtrips(tmp_path):
    task = Task.new("read source", tmp_path, [Step(0, "read", request_id="original-request")])
    original = asdict(task)
    original.pop("schema_version")
    original.pop("budget_events")  # Added optional fields receive their defaults.
    tasks_dir().mkdir(parents=True)
    path = tasks_dir() / f"{task.id}.json"
    path.write_text(json.dumps(original), encoding="utf-8")
    before = path.read_bytes()
    migrated = load(task.id)
    assert path.read_bytes() == before
    assert migrated.schema_version == 2 and migrated.budget_events == []
    assert migrated.steps[0].request_id == "original-request"
    save(migrated)
    assert json.loads(path.read_text())["schema_version"] == 2
    assert load(task.id) == migrated


@pytest.mark.parametrize("change", [
    {"schema_version": 3}, {"schema_version": True}, {"budget_spent": float("nan")},
    {"steps": [{}]}, {"status": "invented"}, {"current_index": -1},
    {"budget_events": [None]}, {"unexpected_field": "value"},
])
def test_invalid_task_state_refused_without_modifying_file(tmp_path, change):
    task = Task.new("read", tmp_path, [])
    path = save(task)
    data = asdict(task)
    data.update(change)
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(StateError):
        load(task.id)
    assert path.read_bytes() == before


def test_malformed_task_json_is_actionable(tmp_path):
    task = Task.new("read", tmp_path, [])
    path = save(task)
    path.write_text('{"id":', encoding="utf-8")
    with pytest.raises(StateError, match="restore a known-good"):
        load(task.id)


def test_session_input_call_correlation_redaction_and_export(tmp_path):
    journal = SessionJournal.create(tmp_path, session_id="my-request_1")
    secret = "sk-" + "S" * 25
    journal.append("user_input", {"query": f"inspect {secret}", "api_key": "private-value"})
    journal.append("model_request", {"messages": [{"role": "user", "content": "read file"}]}, call_id="model.1")
    assert journal.read().pending_call_ids == ["model.1"]
    journal.append("model_response", {"text": "done", "tokens_in": 12}, call_id="model.1", outcome="success")
    snapshot = SessionJournal.open(journal.session_id).read()
    assert snapshot.pending_call_ids == []
    assert snapshot.events[1]["payload"]["api_key"] == "[REDACTED]"
    assert secret not in journal.path.read_text()
    assert snapshot.events[-1]["payload"]["tokens_in"] == 12
    assert len({event["event_id"] for event in snapshot.events}) == 4
    assert [event["sequence"] for event in snapshot.events] == [1, 2, 3, 4]
    target = journal.export(tmp_path / "session-export.json")
    exported = json.loads(target.read_text())
    assert not exported["replay_supported"] and not exported["incomplete_tail"]
    assert list_sessions()[0]["session_id"] == journal.session_id
    with pytest.raises(FileExistsError):
        journal.export(target)


def test_partial_tail_is_not_trusted_or_automatically_replayed(tmp_path):
    journal = SessionJournal.create(tmp_path)
    journal.append("tool_request", {"tool": "edit_node"}, call_id="edit.1")
    committed = journal.path.read_bytes()
    # Even syntactically complete JSON without its terminal newline is uncommitted.
    tail = b'{"event_type":"tool_result","payload":{"ok":true}}'
    with journal.path.open("ab") as handle:
        handle.write(tail)
    snapshot = journal.read()
    assert snapshot.incomplete_tail and snapshot.tail_bytes == len(tail)
    assert snapshot.pending_call_ids == ["edit.1"]
    assert len(snapshot.events) == 2
    with pytest.raises(StateError, match="never replay"):
        journal.append("completed", {})
    assert journal.path.read_bytes() == committed + tail
    backup = journal.recover_tail()
    assert backup.read_bytes() == tail
    assert journal.read().pending_call_ids == ["edit.1"]
    assert not journal.read().incomplete_tail
    assert journal.read().events[-1]["event_type"] == "tail_recovered"


def test_committed_corrupt_or_future_event_fails_closed(tmp_path):
    journal = SessionJournal.create(tmp_path)
    original = journal.path.read_bytes()
    event = json.loads(original)
    event["schema_version"] = 999
    journal.path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    with pytest.raises(StateError, match="invalid committed session event"):
        SessionJournal.open(journal.session_id)
    journal.path.write_bytes(original + b"not json\n")
    with pytest.raises(StateError):
        journal.recover_tail()


def test_oversized_invalid_events_and_duplicate_session_do_not_append(tmp_path):
    journal = SessionJournal.create(tmp_path, session_id="one")
    original = journal.path.read_bytes()
    with pytest.raises(StateError, match="exceeds"):
        journal.append("user_input", {"query": "x" * MAX_EVENT_BYTES})
    with pytest.raises(StateError, match="unsupported"):
        journal.append("execute_this", {})
    with pytest.raises(StateError, match="finite JSON"):
        journal.append("user_input", {"number": float("inf")})
    with pytest.raises(FileExistsError):
        SessionJournal.create(tmp_path, session_id="one")
    assert journal.path.read_bytes() == original
    for value in ("../bad", "a/b", "", "x" * 65):
        with pytest.raises(ValueError):
            SessionJournal.open(value)


def test_process_append_lease_prevents_concurrent_write(tmp_path):
    journal = SessionJournal.create(tmp_path)
    before = journal.path.read_bytes()
    script = """
import sys
from atomic_flow.sessions import SessionJournal
try:
    SessionJournal.open(sys.argv[1]).append('user_input', {'query': 'concurrent'})
except ValueError as exc:
    assert 'another process' in str(exc)
    raise SystemExit(0)
raise SystemExit(3)
"""
    with journal._lease():
        result = subprocess.run([sys.executable, "-c", script, journal.session_id], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode()
    assert journal.path.read_bytes() == before
    journal.append("user_input", {"query": "after lease release"})
    assert len(journal.read().events) == 2
