"""Partial/uncertain experiments cannot be promoted by a utility report."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("p07_paired_report", Path(__file__).resolve().parents[1] / "tools/p07_paired_report.py")
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)


def experiment():
    frozen = {"model": "anthropic/claude-sonnet-5@default", "provider": "kaggle",
        "runtime_sha256": "a" * 64, "permissions_sha256": "b" * 64, "prompt_profile_sha256": "c" * 64,
        "limits": {"max_calls": 30, "max_input_tokens": 600000, "max_output_tokens": 4096,
                   "max_wall_seconds": 900, "max_reported_cost_usd": .3}}
    tasks, records = [], []
    for index in range(30):
        task = {"instance_id": "task-" + str(index), "base_commit": "d" * 40, "source_sha256": "e" * 64,
                "arm_order": ["baseline", "atomic"] if index % 2 == 0 else ["atomic", "baseline"]}
        tasks.append(task)
        for arm in reporter.ARMS:
            record = {**frozen, "instance_id": task["instance_id"], "arm": arm, "base_commit": task["base_commit"],
                "source_sha256": task["source_sha256"], "order_index": task["arm_order"].index(arm),
                "physical_calls": 1, "attempts": [{"request_outcome": "completed", "cost_usd": .01,
                    "cost_source": "provider", "tokens_in": 100, "tokens_out": 10}],
                "semantic_index_cost_usd": 0, "wall_seconds": index + 1, "human_interventions": 0,
                "status": "candidate_generated", "stop_reason": "finished", "completion_claimed": True,
                "study_scope": "full_atomic_public_journey", "candidate_sha256": "f" * 64,
                "grade": {"instance_id": task["instance_id"], "base_commit": task["base_commit"],
                    "candidate_sha256": "f" * 64, "calibrated": True, "status": "verified_success",
                    "requirements_passed": True, "regressions_passed": True,
                    "evidence_sha256": "1" * 64, "image_digest": "sha256:" + "2" * 64}}
            records.append(copy.deepcopy(record))
    obligations = {name: {"status": "accepted", "evidence_sha256": "3" * 64} for name in reporter.PREREQUISITES}
    obligations["ablations"] = {name: {"status": "complete", "evidence_sha256": "4" * 64,
        "frozen_signature_sha256": reporter.digest(frozen), "predeclared_task_count": 3, "completed_task_count": 3}
        for name in reporter.ABLATIONS}
    return {"schema_version": 1, "frozen": frozen, "tasks": tasks}, records, obligations


def test_complete_measurement_still_requires_review_never_auto_closes_p07():
    manifest, records, obligations = experiment()
    result = reporter.summarize(manifest, records, obligations)
    assert result["status"] == "measurement_complete"
    assert result["p07"] == "OPEN" and result["closure_readiness"] == "requires_independent_review"
    assert result["competitive_advantage"] == "not_assessed"
    baseline = result["metrics"]["baseline"]
    assert baseline["verified_success_count"] == 30
    assert baseline["wall_p50_seconds"] == 15.5
    assert baseline["wall_p95_seconds"] == pytest.approx(28.55)
    assert result["paired_outcomes"]["both_success"] == 30
    assert result["paired_outcomes"]["success_rate_difference_atomic_minus_baseline"] == 0


def test_missing_arm_keeps_full30_denominator_and_partial_metrics():
    manifest, records, obligations = experiment()
    result = reporter.summarize(manifest, records[:-1], obligations)
    assert result["status"] == "incomplete"
    assert result["metrics"]["atomic"]["expected_tasks"] == 30
    assert result["metrics"]["atomic"]["verified_success_rate"] == 29 / 30
    assert result["metrics"]["atomic"]["complete_cost_per_success_usd"] is None
    assert any(issue["reason"] == "missing_expected_arm" for issue in result["issues"])


@pytest.mark.parametrize("change,reason", [
    ({"model": "other"}, "frozen_model_mismatch"),
    ({"permissions_sha256": "9" * 64}, "frozen_permissions_sha256_mismatch"),
    ({"source_sha256": "9" * 64}, "source_identity_mismatch"),
    ({"limits": {"max_calls": 300}}, "frozen_limits_mismatch"),
    ({"study_scope": "source-only development"}, "atomic_is_not_full_public_journey"),
    ({"status": "not_run"}, "nonterminal_or_not_run"),
])
def test_matched_conditions_cannot_silently_drift(change, reason):
    manifest, records, obligations = experiment()
    records[1].update(change)
    result = reporter.summarize(manifest, records, obligations)
    assert result["status"] == "incomplete"
    assert reason in {issue["reason"] for issue in result["issues"]}


@pytest.mark.parametrize("change", [{"cost_usd": None}, {"request_outcome": "unknown"}, {"cost_source": "configured_estimate"}])
def test_unknown_or_estimated_cost_quarantines_even_when_oracle_passes(change):
    manifest, records, obligations = experiment()
    records[0]["attempts"][0].update(change)
    result = reporter.summarize(manifest, records, obligations)
    assert result["admission"] == "quarantine" and result["status"] == "incomplete"
    assert result["metrics"]["baseline"]["complete_cost_per_success_usd"] is None


def test_failed_attempt_spend_and_false_completion_are_retained():
    manifest, records, obligations = experiment()
    records[0]["grade"].update(status="requirement_failed", requirements_passed=False)
    records[0]["human_interventions"] = 2
    records[0]["attempts"].append({"request_outcome": "completed", "cost_usd": .2,
        "cost_source": "provider", "tokens_in": 300, "tokens_out": 50})
    records[0]["physical_calls"] = 2
    result = reporter.summarize(manifest, records, obligations)
    metrics = result["metrics"]["baseline"]
    assert result["status"] == "measurement_complete"  # A measured failed solution is a real result.
    assert metrics["verified_success_count"] == 29 and metrics["false_completion_count"] == 1
    assert metrics["known_total_cost_usd"] == pytest.approx(.5)
    assert metrics["complete_cost_per_success_usd"] == pytest.approx(.5 / 29)
    assert metrics["human_interventions_known"] == 2 and metrics["physical_calls_observed"] == 31
    assert result["paired_outcomes"]["atomic_only"] == 1
    assert result["paired_outcomes"]["success_rate_difference_atomic_minus_baseline"] == 1 / 30


def test_partial_oracle_cannot_become_verified_success_or_known_false_completion():
    manifest, records, obligations = experiment()
    records[0]["grade"]["calibrated"] = False
    result = reporter.summarize(manifest, records, obligations)
    assert result["status"] == "incomplete"
    assert result["metrics"]["baseline"]["verified_success_count"] == 29
    assert result["metrics"]["baseline"]["false_completion_unknown"] == 1


def test_duplicate_record_cannot_add_success_but_spend_is_not_dropped():
    manifest, records, obligations = experiment()
    records.append(copy.deepcopy(records[0]))
    result = reporter.summarize(manifest, records, obligations)
    metrics = result["metrics"]["baseline"]
    assert result["status"] == "incomplete" and metrics["observed_records"] == 31
    assert metrics["verified_success_count"] == 29
    assert metrics["known_total_cost_usd"] == pytest.approx(.31)


def test_missing_holdout_or_ablations_blocks_closure_readiness():
    manifest, records, _ = experiment()
    result = reporter.summarize(manifest, records)
    assert result["status"] == "measurement_complete" and result["closure_readiness"] == "incomplete"
    assert "fresh_holdout" in result["missing_obligations"]
    assert {"ablation:" + name for name in reporter.ABLATIONS} <= set(result["missing_obligations"])


def test_ablation_with_different_hidden_limits_is_not_accepted():
    manifest, records, obligations = experiment()
    obligations["ablations"]["critic"]["frozen_signature_sha256"] = "9" * 64
    result = reporter.summarize(manifest, records, obligations)
    assert result["missing_obligations"] == ["ablation:critic"]


def test_three_task_pilot_cannot_be_called_the_full_paired_report():
    manifest, records, obligations = experiment()
    manifest["tasks"] = manifest["tasks"][:3]
    with pytest.raises(ValueError, match="30 unique"):
        reporter.summarize(manifest, records[:6], obligations)


def test_v2_oracle_manifest_preserves_sources_and_documents_every_label_change():
    root = Path(__file__).resolve().parents[1]
    raw = (root / "benchmarks/stage4/corpus.json").read_bytes()
    new_raw = (root / "benchmarks/stage4/corpus-p07-v2.json").read_bytes()
    migrations = json.loads((root / "benchmarks/stage4/p07-manifest-migrations.json").read_text())
    old, new = json.loads(raw), json.loads(new_raw)
    assert migrations["hash_contract"] == "git_text_lf_bytes"
    assert hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() == migrations["original_sha256"]
    assert hashlib.sha256(new_raw.replace(b"\r\n", b"\n")).hexdigest() == migrations["new_sha256"]
    assert len(old["tasks"]) == len(new["tasks"]) == 30
    changed = {}
    for prior, updated in zip(old["tasks"], new["tasks"], strict=True):
        assert prior.keys() == updated.keys()
        fields = {key for key in prior if prior[key] != updated[key]}
        if fields:
            changed[prior["instance_id"]] = fields
        assert all(prior[key] == updated[key] for key in prior if key not in {"FAIL_TO_PASS", "PASS_TO_PASS"})
    assert changed == {"pytest-dev__pytest-5227": {"PASS_TO_PASS"}, "sympy__sympy-18057": {"FAIL_TO_PASS"}}
    assert not migrations["tasks_added"] and not migrations["tasks_removed"]
    for migration in migrations["migrations"]:
        assert reporter.sha256(migration["evidence"]["test_patch_sha256"])
        assert reporter.sha256(migration["evidence"]["archive_sha256"])
        assert set(migration["evidence"]["raw_log_sha256"]) == {"base", "gold", "wrong"}


def test_corrected_labels_do_not_remove_any_genuine_regression():
    root = Path(__file__).resolve().parents[1]
    old = {task["instance_id"]: task for task in json.loads((root / "benchmarks/stage4/corpus.json").read_text())["tasks"]}
    new = {task["instance_id"]: task for task in json.loads((root / "benchmarks/stage4/corpus-p07-v2.json").read_text())["tasks"]}
    task_id = "pytest-dev__pytest-5227"
    removed = set(old[task_id]["PASS_TO_PASS"]) - set(new[task_id]["PASS_TO_PASS"])
    assert removed == {"[100%]", "[", "[100%]------------------------------"}
    assert all("::" in item for item in new[task_id]["PASS_TO_PASS"])
    task_id = "sympy__sympy-18057"
    assert new[task_id]["FAIL_TO_PASS"] == old[task_id]["FAIL_TO_PASS"] + ["test_non_string_equality"]
    assert new[task_id]["PASS_TO_PASS"] == old[task_id]["PASS_TO_PASS"]
    assert "+def test_non_string_equality(" in new[task_id]["test_patch"]
