"""Offline import-review regressions for durable reconciliation boundaries."""
import json

import pytest
from test_reconcile import EVIDENCE, unknown_charge_task

from atomic_flow.agents.task import load
from atomic_flow.agents.task_budget import check_accounting
from atomic_flow.budget import SpendLedger
from atomic_flow.errors import BudgetExceeded
from atomic_flow.reconcile import reconcile_call
from atomic_flow.sessions import SessionJournal
from atomic_flow.state import StateError


def settle(task, call_id="call_x", **kwargs):
    return reconcile_call(task.id, call_id, "charge", evidence=EVIDENCE,
                          actor="owner", reason="statement", cost_usd=kwargs.pop("cost", 0.4), **kwargs)


def test_daily_cap_refuses_explicitly_unknown_pricing_before_reservation():
    ledger = SpendLedger(10)
    with pytest.raises(BudgetExceeded, match="configure model prices"):
        ledger.reserve(call_id="unknown_price", reserved_usd=0, prices_known=False)
    assert not ledger.path.exists()


def test_cross_day_settlement_repairs_original_day_and_does_not_charge_today(tmp_path):
    task, _ = unknown_charge_task(tmp_path)
    ledger = SpendLedger(10)
    original = ledger.path.with_name("2020-01-01.jsonl")
    ledger.path.replace(original)
    settle(task)
    assert not ledger.path.exists()
    rows = [json.loads(line) for line in original.read_text().splitlines()]
    assert rows[-1]["status"] == "reconciled" and rows[-1]["cost_usd"] == 0.4
    settle(task)
    assert len(original.read_text().splitlines()) == len(rows)


def test_same_evidence_cannot_change_amount_or_report_false_result(tmp_path):
    task, _ = unknown_charge_task(tmp_path)
    settle(task)
    with pytest.raises(StateError, match="different settled cost"):
        settle(task, cost=0.1)
    assert load(task.id).budget_spent == 0.4
    assert SpendLedger(10).today_total() == 0.4


def test_missing_or_foreign_ledger_identity_never_completes_task(tmp_path):
    task, _ = unknown_charge_task(tmp_path)
    ledger = SpendLedger(10)
    rows = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    rows[0]["task_id"] = "task_foreign"
    ledger.path.write_text(json.dumps(rows[0]) + "\n")
    with pytest.raises(StateError, match="different task"):
        settle(task)
    with pytest.raises(BudgetExceeded, match="unresolved"):
        check_accounting(load(task.id))
    ledger.path.unlink()
    with pytest.raises(StateError, match="original spend reservation"):
        settle(task)


def test_crash_after_ledger_before_session_blocks_until_same_evidence_repair(tmp_path, monkeypatch):
    import atomic_flow.reconcile as module

    task, _ = unknown_charge_task(tmp_path)
    real = module._session_event
    monkeypatch.setattr(module, "_session_event", lambda *args: (_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(OSError):
        settle(task)
    interrupted = load(task.id)
    assert interrupted.budget_spent == 0
    assert interrupted.budget_events[0]["reconciliation"]["pending"]
    with pytest.raises(BudgetExceeded):
        check_accounting(interrupted)
    monkeypatch.setattr(module, "_session_event", real)
    settle(task)
    check_accounting(load(task.id))
    assert load(task.id).budget_spent == 0.4
    assert SpendLedger(10).today_total() == 0.4


def test_crash_after_session_before_final_task_save_remains_blocked(tmp_path, monkeypatch):
    import atomic_flow.reconcile as module

    task, _ = unknown_charge_task(tmp_path)
    real_save = module.save_task
    writes = []

    def save_with_final_failure(value):
        writes.append(value.budget_events[0]["status"])
        if len(writes) == 2:
            raise OSError("final task write failed")
        return real_save(value)

    monkeypatch.setattr(module, "save_task", save_with_final_failure)
    with pytest.raises(OSError):
        settle(task)
    assert writes == ["unknown", "completed"]
    with pytest.raises(BudgetExceeded):
        check_accounting(load(task.id))
    assert SessionJournal.open(task.id).read().unresolved_call_ids == []
    monkeypatch.setattr(module, "save_task", real_save)
    settle(task)
    check_accounting(load(task.id))
    assert load(task.id).budget_spent == 0.4
    assert SpendLedger(10).today_total() == 0.4


def test_effect_rejects_missing_charge_only_and_already_terminal_calls(tmp_path):
    task, journal = unknown_charge_task(tmp_path)
    journal.append("tool_request", {"tool": "write"}, call_id="write_done")
    journal.append("tool_result", {"ok": True}, call_id="write_done", outcome="success")
    for call in ("missing", "call_x", "write_done"):
        with pytest.raises(StateError, match="actual unresolved effect"):
            reconcile_call(task.id, call, "effect", evidence=EVIDENCE, actor="owner", reason="checked")
    journal.append("tool_effect_unknown", {}, call_id="write_unknown", outcome="unknown")
    reconcile_call(task.id, "write_unknown", "effect", evidence=EVIDENCE, actor="owner", reason="checked")
    reconcile_call(task.id, "write_unknown", "effect", evidence=EVIDENCE, actor="owner", reason="checked")
    with pytest.raises(StateError, match="different evidence"):
        reconcile_call(task.id, "write_unknown", "effect", evidence=b"other", actor="owner", reason="checked")
    assert "call_x" in SessionJournal.open(task.id).read().unresolved_call_ids
