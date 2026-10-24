"""Cross-midnight records preserve the original reservation and owner identity."""
import json

import pytest

from atomic_flow.budget import SpendLedger
from atomic_flow.state import StateError


@pytest.fixture
def clocked_ledger(tmp_path, monkeypatch):
    day = ["2026-01-01"]
    monkeypatch.setattr(SpendLedger, "path", property(lambda self: self.dir / (day[0] + ".jsonl")))
    ledger = SpendLedger(10, tmp_path / "ledger")
    return ledger, day


def test_record_and_unknown_reconciliation_after_midnight_use_original_day(clocked_ledger):
    ledger, day = clocked_ledger
    ledger.reserve(task_id="task_a", call_id="call_a", reserved_usd=0.5)
    original = ledger.path
    day[0] = "2026-01-02"
    ledger.record(task_id="task_a", call_id="call_a", cost_usd=None, reserved_usd=0.5, status="unknown")
    assert not ledger.path.exists() and ledger.today_total() == 0
    ledger.settle(task_id="task_a", call_id="call_a", cost_usd=0.3,
                  status="reconciled", reconciliation_id="reconcile_a")
    assert not ledger.path.exists()
    rows = [json.loads(line) for line in original.read_text().splitlines()]
    assert [row["status"] for row in rows] == ["in_flight", "unknown", "reconciled"]
    assert rows[-1]["cost_usd"] == 0.3


def test_completed_response_after_midnight_replaces_original_hold(clocked_ledger):
    ledger, day = clocked_ledger
    ledger.reserve(task_id="task_a", call_id="call_a", reserved_usd=0.5)
    original = ledger.path
    day[0] = "2026-01-02"
    ledger.record(task_id="task_a", call_id="call_a", cost_usd=0.2, status="completed")
    assert not ledger.path.exists()
    assert json.loads(original.read_text().splitlines()[-1])["cost_usd"] == 0.2
    day[0] = "2026-01-01"
    assert ledger.today_total(strict=True) == 0.2


def test_foreign_owner_and_duplicate_day_refused_without_writes(clocked_ledger):
    ledger, day = clocked_ledger
    ledger.reserve(task_id="task_a", call_id="call_a", reserved_usd=0.5)
    original = ledger.path
    before = original.read_bytes()
    day[0] = "2026-01-02"
    with pytest.raises(StateError, match="different task"):
        ledger.record(task_id="task_b", call_id="call_a", cost_usd=0.2)
    assert original.read_bytes() == before and not ledger.path.exists()
    ledger.path.write_bytes(before)
    with pytest.raises(StateError, match="multiple ledger days"):
        ledger.record(task_id="task_a", call_id="call_a", cost_usd=0.2)
    assert original.read_bytes() == ledger.path.read_bytes() == before


def test_unreserved_legacy_record_keeps_compatibility(clocked_ledger):
    ledger, _ = clocked_ledger
    ledger.record(cost_usd=0.1)
    ledger.record(call_id="legacy_call", cost_usd=0.2)
    assert ledger.today_total(strict=True) == pytest.approx(0.3)


def test_late_response_cannot_override_host_reconciled_cost(clocked_ledger):
    ledger, _ = clocked_ledger
    ledger.reserve(task_id="task_a", call_id="call_a", reserved_usd=0.5)
    ledger.settle(task_id="task_a", call_id="call_a", cost_usd=0.3,
                  status="reconciled", reconciliation_id="reconcile_a")
    before = ledger.path.read_bytes()
    with pytest.raises(StateError, match="reconciled spend"):
        ledger.record(task_id="task_a", call_id="call_a", cost_usd=0.1)
    ledger.record(task_id="task_a", call_id="call_a", cost_usd=0.3)
    assert ledger.path.read_bytes() == before
