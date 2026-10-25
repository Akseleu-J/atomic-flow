"""Receipts bind observations, never grant authority or trigger replay."""
from copy import deepcopy

import pytest

from atomic_flow.agents.step_evidence import build_receipt, receipt_matches, validate_receipt
from atomic_flow.agents.task import Step

TASK = "task_abcdef123456"
MANIFEST = {"a.py": "a" * 40}


def sample(**kwargs):
    step = Step(0, "Inspect a source file", tools_needed=["read_file"], verify_with="none", request_id="req_sample")
    receipt = build_receipt(TASK, step, before_manifest=MANIFEST, after_manifest=MANIFEST,
                            approved_scope=["a.py"], **kwargs)
    return step, receipt


def test_exact_receipt_roundtrip_and_legacy_is_not_replay_authority():
    step, receipt = sample(graph_revision="generation:3", context_revision="context:4")
    validate_receipt(receipt)
    assert receipt_matches(receipt, step, MANIFEST, task_id=TASK, approved_scope=["a.py"]) == (True, [])
    validate_receipt({})
    assert receipt_matches({}, step, MANIFEST, task_id=TASK)[0] is False


@pytest.mark.parametrize("change", ["source", "contract", "task", "request", "scope"])
def test_stale_binding_is_diagnostic_only(change):
    step, receipt = sample()
    manifest, task_id, scope = dict(MANIFEST), TASK, ["a.py"]
    if change == "source":
        manifest["a.py"] = "b" * 40
    elif change == "contract":
        step.expected_outcome = "A new acceptance requirement"
    elif change == "task":
        task_id = "task_123456abcdef"
    elif change == "request":
        step.request_id = "req_other"
    else:
        scope.append("b.py")
    state = deepcopy(step)
    ok, reasons = receipt_matches(receipt, step, manifest, task_id=task_id, approved_scope=scope)
    assert not ok and reasons
    assert step == state  # Classification never resets completed work or mutates state.


def test_transcript_digest_never_includes_raw_args_outputs_or_errors():
    record = {"tool": "read_file", "args": {"secret": "FIRST"},
              "result_summary": {"outcome": "success", "status": "read", "error": "FIRST"}, "output": "FIRST"}
    _, first = sample(tool_calls=[record])
    record["args"]["secret"] = "SECOND"
    record["result_summary"]["error"] = "SECOND"
    record["output"] = "SECOND"
    _, second = sample(tool_calls=[record])
    assert first == second
    assert "FIRST" not in str(first) and "SECOND" not in str(second)
    record["result_summary"]["outcome"] = "denied"
    _, third = sample(tool_calls=[record])
    assert third["tool_transcript_sha256"] != first["tool_transcript_sha256"]


@pytest.mark.parametrize("field,value", [("version", True), ("step_index", False),
                                         ("task_id", "../../task"), ("graph_revision", "x" * 300),
                                         ("linked_test_nodes", ["x"] * 65)])
def test_malformed_receipts_fail_closed(field, value):
    _, receipt = sample()
    receipt[field] = value
    with pytest.raises(ValueError):
        validate_receipt(receipt)


def test_integrity_and_unknown_fields_are_rejected():
    _, receipt = sample()
    receipt["verification"]["outcome"] = "passed"
    with pytest.raises(ValueError, match="integrity"):
        validate_receipt(receipt)
    _, receipt = sample()
    receipt["authority"] = True
    with pytest.raises(ValueError, match="fields"):
        validate_receipt(receipt)


def test_unsafe_or_invalid_manifest_rejected_before_receipt():
    step, _ = sample()
    for manifest in ({"../secret.py": "a" * 40}, {"a.py": None}, {"a.py": True}):
        with pytest.raises(ValueError):
            build_receipt(TASK, step, before_manifest=manifest, after_manifest=MANIFEST, approved_scope=["a.py"])


def test_failed_or_unavailable_check_remains_observation_not_success():
    for outcome in ("failed", "unavailable", "not_run"):
        step, receipt = sample(verification_outcome=outcome)
        assert receipt["verification"]["outcome"] == outcome
        # Matching means identity/freshness only; callers must inspect outcome.
        assert receipt_matches(receipt, step, MANIFEST, task_id=TASK)[0]


def test_explicit_premises_allow_unrelated_edits_but_detect_changes_and_new_files():
    step, receipt = sample(premise_paths=["a.py", "missing.py"])
    assert receipt["observed_hashes"] == {"a.py": "a" * 40, "missing.py": None}
    unrelated = {**MANIFEST, "other.py": "b" * 40}
    assert receipt_matches(receipt, step, unrelated, task_id=TASK)[0]
    assert not receipt_matches(receipt, step, {**unrelated, "a.py": "c" * 40}, task_id=TASK)[0]
    assert not receipt_matches(receipt, step, {**unrelated, "missing.py": "d" * 40}, task_id=TASK)[0]
    assert not receipt_matches(receipt, step, {}, task_id=TASK)[0]


def test_unknown_premise_coverage_uses_full_manifest_binding():
    step, receipt = sample()
    assert receipt["observed_hashes"] is None
    assert not receipt_matches(receipt, step, {**MANIFEST, "other.py": "b" * 40}, task_id=TASK)[0]


def test_invalid_or_unbounded_premise_paths_fail_closed():
    for paths in (["../secret.py"], [f"p{i}.py" for i in range(129)]):
        with pytest.raises(ValueError):
            sample(premise_paths=paths)
