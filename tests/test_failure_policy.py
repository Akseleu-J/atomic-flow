"""P06-3: failure classification table decides stop/continue without text heuristics."""
import pytest

from atomic_flow.agents.failure_policy import ASK_USER, CONTINUE_REPAIR, RECONCILE_HOLD, REREAD_REVIEW, STOP, classify


def test_deterministic_test_failure_allows_bounded_repair():
    decision = classify({"kind": "deterministic_test_failure", "remaining_budget_usd": 1.0})
    assert decision["action"] == CONTINUE_REPAIR
    assert decision["repair_budget_usd"] == 0.25
    assert "envelope_preserved" in decision["requires"]


def test_bounded_repair_without_remaining_budget_stops():
    assert classify({"kind": "deterministic_test_failure", "remaining_budget_usd": 0})["action"] == STOP


@pytest.mark.parametrize("kind,expected", [
    ("source_conflict", REREAD_REVIEW),
    ("stale_approval_digest", REREAD_REVIEW),
    ("missing_dependency", ASK_USER),
    ("unknown_submission", RECONCILE_HOLD),
    ("unknown_charge", RECONCILE_HOLD),
    ("unknown_effect", RECONCILE_HOLD),
    ("authority_expansion_requested", ASK_USER),
    ("scope_violation_detected", STOP),
    ("budget_exhausted", STOP),
    ("deadline_reached", STOP),
    ("no_progress", STOP),
    ("cancel_requested", STOP),
])
def test_matrix_kinds_map_to_mandated_actions(kind, expected):
    assert classify({"kind": kind})["action"] == expected


def test_unknown_kind_fails_closed_to_stop():
    decision = classify({"kind": "model_said_something_new"})
    assert decision["action"] == STOP and "contract change required" in decision["reason"]
