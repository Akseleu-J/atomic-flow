"""W2 host reconciliation operation: evidence-backed, idempotent, never replays.

Covers reconcile_call and the `atomic task --reconcile` CLI wrapper: settling an
unknown charge across task/ledger/session, crash-repair idempotency, refusal of
wrong state, and the independence of charge vs effect holds.
"""
import json

import pytest

from atomic_flow.agents.task import Task, load, save
from atomic_flow.budget import SpendLedger
from atomic_flow.cli import main as cli_main
from atomic_flow.reconcile import reconcile_call
from atomic_flow.sessions import SessionJournal
from atomic_flow.state import StateError

EVIDENCE = b"provider dashboard export confirming the settled amount"


def unknown_charge_task(tmp_path, call_id="call_x", reserved=0.5):
    task = Task.new("report", tmp_path, [])
    save(task)
    journal = SessionJournal.create(tmp_path, session_id=task.id)
    journal.append("model_request", {"role": "answerer"}, call_id=call_id)
    journal.append("model_failed", {"error_type": "TimeoutError"}, call_id=call_id, outcome="unknown")
    SpendLedger(10).reserve(task_id=task.id, call_id=call_id, reserved_usd=reserved)
    task.budget_events.append({"id": call_id, "phase": "step", "status": "unknown",
                               "reserved_usd": reserved, "backend": "fake", "model": "m"})
    save(task)
    return task, journal


def test_charge_reconciliation_settles_all_three_artifacts(tmp_path):
    task, journal = unknown_charge_task(tmp_path)
    assert "call_x" in SessionJournal.open(task.id).read().unresolved_call_ids
    result = reconcile_call(task.id, "call_x", "charge", evidence=EVIDENCE,
                            actor="owner", reason="provider statement", cost_usd=0.4,
                            daily_limit_usd=0.001)  # settlement is not blocked by daily caps
    stored = load(task.id)
    event = stored.budget_events[0]
    assert event["status"] == "completed" and event["cost_usd"] == 0.4
    assert stored.budget_spent == pytest.approx(0.4)
    assert result["reconciliation_id"].startswith("reconcile_")
    assert result["artifacts_updated"] == {"task": True, "ledger": True, "session": True}
    # The settled row replaces the reservation under the same call_id.
    assert SpendLedger(10).today_total() == pytest.approx(0.4)
    assert SessionJournal.open(task.id).read().unresolved_call_ids == []


def test_reconciliation_is_idempotent_and_repairs_crash_gaps(tmp_path):
    task, _ = unknown_charge_task(tmp_path)
    first = reconcile_call(task.id, "call_x", "charge", evidence=EVIDENCE,
                           actor="owner", reason="ok", cost_usd=0.4)
    second = reconcile_call(task.id, "call_x", "charge", evidence=EVIDENCE,
                            actor="owner", reason="ok", cost_usd=0.4)
    assert second["reconciliation_id"] == first["reconciliation_id"]
    journal = SessionJournal.open(task.id)
    events = [e for e in journal.read().events if e["event_type"] == "host_reconciliation"]
    assert len(events) == 1                       # no duplicate observation
    assert SpendLedger(10).today_total() == pytest.approx(0.4)

    # Crash gap: the task event landed but the session write did not.
    task2, _ = unknown_charge_task(tmp_path, call_id="call_y")
    reconcile_call(task2.id, "call_y", "charge", evidence=EVIDENCE, actor="owner", reason="ok", cost_usd=0.3)
    journal2 = SessionJournal.open(task2.id)
    lines = journal2.path.read_bytes().splitlines(keepends=True)
    kept = [line for line in lines if b"host_reconciliation" not in line]
    journal2.path.write_bytes(b"".join(kept))
    journal2 = SessionJournal.open(task2.id)
    assert "call_y" in journal2.read().unresolved_call_ids
    repaired = reconcile_call(task2.id, "call_y", "charge", evidence=EVIDENCE,
                              actor="owner", reason="ok", cost_usd=0.3)
    assert repaired["artifacts_updated"]["session"] is True
    assert SessionJournal.open(task2.id).read().unresolved_call_ids == []


@pytest.mark.parametrize("kwargs,match", [
    ({"evidence": EVIDENCE, "actor": "o", "reason": "r", "cost_usd": None}, "settled cost"),
    ({"evidence": EVIDENCE, "actor": "o", "reason": "r", "cost_usd": -1.0}, "settled cost"),
    ({"evidence": EVIDENCE, "actor": "", "reason": "r", "cost_usd": 0.1}, "actor and reason"),
])
def test_reconciliation_input_validation(tmp_path, kwargs, match):
    task, _ = unknown_charge_task(tmp_path)
    with pytest.raises(StateError, match=match):
        reconcile_call(task.id, "call_x", "charge", **kwargs)


def test_reconciliation_refuses_wrong_state(tmp_path):
    task, _ = unknown_charge_task(tmp_path)
    with pytest.raises(StateError, match="no budget event"):
        reconcile_call(task.id, "call_missing", "charge", evidence=EVIDENCE, actor="o", reason="r", cost_usd=0.1)
    with pytest.raises(StateError, match="scope"):
        reconcile_call(task.id, "call_x", "charges", evidence=EVIDENCE, actor="o", reason="r", cost_usd=0.1)
    reconcile_call(task.id, "call_x", "charge", evidence=EVIDENCE, actor="o", reason="r", cost_usd=0.1)
    with pytest.raises(StateError, match="different evidence"):
        reconcile_call(task.id, "call_x", "charge", evidence=b"other evidence", actor="o", reason="r", cost_usd=0.2)
    settled = load(task.id).budget_events[0]
    assert settled["cost_usd"] == 0.1            # refused redo did not change the settlement
    task2, _ = unknown_charge_task(tmp_path, call_id="call_z")
    task2.budget_events[0]["status"] = "completed"
    save(task2)
    with pytest.raises(StateError, match="only unknown"):
        reconcile_call(task2.id, "call_z", "charge", evidence=EVIDENCE, actor="o", reason="r", cost_usd=0.1)


def test_effect_reconciliation_requires_session_and_clears_only_effects(tmp_path):
    task = Task.new("report", tmp_path, [])
    save(task)
    with pytest.raises(StateError, match="session observations"):
        reconcile_call(task.id, "write.1", "effect", evidence=EVIDENCE, actor="o", reason="r")

    journal = SessionJournal.create(tmp_path, session_id=task.id)
    journal.append("model_request", {"role": "a"}, call_id="model_1")
    journal.append("model_failed", {"e": 1}, call_id="model_1", outcome="unknown")
    journal.append("tool_request", {"tool": "write"}, call_id="write.1")
    journal.append("tool_effect_unknown", {"reason": "timeout"}, call_id="write.1")
    result = reconcile_call(task.id, "write.1", "effect", evidence=EVIDENCE, actor="o", reason="fs verified")
    assert result["artifacts_updated"] == {"task": False, "ledger": False, "session": True}
    assert SessionJournal.open(task.id).read().unresolved_call_ids == ["model_1"]
    assert load(task.id).budget_spent == 0       # effect scope never touches money


def test_cli_reconcile_wrapper(tmp_path, capsys):
    task, _ = unknown_charge_task(tmp_path)
    evidence = tmp_path / "evidence.txt"
    evidence.write_bytes(EVIDENCE)
    code = cli_main(["task", "--reconcile", "call_x", "--task-id", task.id, "--scope", "charge",
                     "--cost-usd", "0.4", "--evidence-file", str(evidence),
                     "--actor", "owner", "--reason", "provider statement"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["settled_cost_usd"] == 0.4 and payload["scope"] == "charge"
    assert load(task.id).budget_events[0]["status"] == "completed"
    missing = cli_main(["task", "--reconcile", "call_x", "--scope", "charge", "--cost-usd", "0.4"])
    assert missing == 2 and "--task-id" in capsys.readouterr().err
