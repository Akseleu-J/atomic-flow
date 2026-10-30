"""Deterministic utility accounting; metadata completeness never auto-closes P07.

Inputs are host evidence records, not model self-reports. No target imports,
provider requests, oracle execution or automatic imputation happens here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

ARMS = ("baseline", "atomic")
ABLATIONS = ("graph_retrieval", "semantic_layer", "task_dag", "critic")
PREREQUISITES = ("e01", "isolated_execution", "public_full_journey", "fresh_holdout")
TERMINAL = {"candidate_ungraded", "candidate_generated", "generation_failed", "no_candidate", "call_limit", "paused", "failed", "done"}


def amount(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def sha256(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def percentile(values, probability):
    """Linear interpolation over sorted observations; denominator is explicit."""
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * probability
    lower = int(index)
    return values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (index - lower)


def independent_grade(record):
    grade = record.get("grade")
    patch = record.get("candidate_sha256")
    if not patch:
        return "no_candidate", False
    if not sha256(patch) or not isinstance(grade, dict):
        return "missing_oracle", False
    if (grade.get("candidate_sha256") != patch or grade.get("instance_id") != record.get("instance_id")
            or grade.get("base_commit") != record.get("base_commit") or grade.get("calibrated") is not True
            or not sha256(grade.get("evidence_sha256"))
            or not isinstance(grade.get("image_digest"), str) or not grade["image_digest"].startswith("sha256:")
            or not sha256(grade["image_digest"][7:])):
        return "unbound_oracle", False
    status = grade.get("status")
    if status == "verified_success" and grade.get("requirements_passed") is True and grade.get("regressions_passed") is True:
        return "verified_success", True
    if status == "requirement_failed":
        return "requirement_failed", False
    return "infrastructure_or_unknown", False


def summarize(manifest, records, obligations=None):
    """One expected row per task×arm; retain spend from duplicate/extra records."""
    if manifest.get("schema_version") != 1 or not isinstance(records, list):
        raise ValueError("versioned host manifest and record list required")
    tasks = manifest.get("tasks", [])
    if len(tasks) != 30 or len({t["instance_id"] for t in tasks}) != 30:
        raise ValueError("P07 report requires exactly 30 unique expected tasks; no pilot denominator substitution")
    frozen = manifest.get("frozen", {})
    required = ("model", "provider", "runtime_sha256", "limits", "permissions_sha256", "prompt_profile_sha256")
    if any(key not in frozen for key in required) or any(not sha256(frozen[key]) for key in
            ("runtime_sha256", "permissions_sha256", "prompt_profile_sha256")):
        raise ValueError("frozen model/provider/runtime/caps/authority/profile identity required")
    expected = {task["instance_id"]: task for task in tasks}
    if any(not sha256(task.get("source_sha256")) or len(task.get("base_commit", "")) != 40
           or any(c not in "0123456789abcdef" for c in task["base_commit"]) for task in tasks):
        raise ValueError("every task needs pinned base and source-manifest digest")
    counts = Counter((record.get("instance_id"), record.get("arm")) for record in records)
    issues, observations = [], []
    all_attempts = []
    for record_index, record in enumerate(records):
        key = (record.get("instance_id"), record.get("arm"))
        task = expected.get(key[0])
        row_issues = []
        if task is None or key[1] not in ARMS:
            row_issues.append("unexpected_task_or_arm")
        if counts[key] != 1:
            row_issues.append("duplicate_task_arm")
        if task is not None:
            if record.get("base_commit") != task["base_commit"] or record.get("source_sha256") != task["source_sha256"]:
                row_issues.append("source_identity_mismatch")
            order = task.get("arm_order")
            if (order not in (["baseline", "atomic"], ["atomic", "baseline"])
                    or key[1] not in ARMS or record.get("order_index") != order.index(key[1])):
                row_issues.append("arm_order_missing_or_mismatch")
        for field in required:
            if record.get(field) != frozen[field]:
                row_issues.append("frozen_" + field + "_mismatch")
        attempts = record.get("attempts")
        if not isinstance(attempts, list):
            attempts = []
            row_issues.append("physical_attempt_receipts_missing")
        elif record.get("physical_calls") != len(attempts):
            row_issues.append("physical_call_count_mismatch")
        complete_cost = True
        cost, tokens_in, tokens_out = 0.0, 0, 0
        for attempt in attempts:
            all_attempts.append(attempt)
            known_refusal = attempt.get("request_outcome") in {"not_sent", "refused"} and attempt.get("cost_usd") == 0
            if (not amount(attempt.get("cost_usd")) or attempt.get("request_outcome") == "unknown"
                    or not known_refusal and attempt.get("cost_source") != "provider"):
                complete_cost = False
            elif amount(attempt.get("cost_usd")):
                cost += attempt["cost_usd"]
            for field in ("tokens_in", "tokens_out"):
                if type(attempt.get(field)) is not int or attempt[field] < 0:
                    row_issues.append(field + "_unknown")
            if type(attempt.get("tokens_in")) is int and attempt["tokens_in"] >= 0:
                tokens_in += attempt["tokens_in"]
            if type(attempt.get("tokens_out")) is int and attempt["tokens_out"] >= 0:
                tokens_out += attempt["tokens_out"]
        if not complete_cost:
            row_issues.append("unknown_charge_quarantine")
        semantic_cost = record.get("semantic_index_cost_usd")
        if not amount(semantic_cost):
            row_issues.append("semantic_index_cost_unknown")
        else:
            cost += semantic_cost
        wall = record.get("wall_seconds")
        if not amount(wall):
            row_issues.append("wall_seconds_unknown")
        interventions = record.get("human_interventions")
        if type(interventions) is not int or interventions < 0:
            row_issues.append("human_interventions_unknown")
        if record.get("status") not in TERMINAL:
            row_issues.append("nonterminal_or_not_run")
        grade_status, success = independent_grade(record)
        if grade_status not in {"no_candidate", "verified_success", "requirement_failed"}:
            row_issues.append("independent_grade_incomplete")
        claimed = record.get("completion_claimed")
        if type(claimed) is not bool:
            row_issues.append("completion_claim_unknown")
        false_completion = (claimed and not success) if type(claimed) is bool and grade_status in {
            "no_candidate", "verified_success", "requirement_failed"} else None
        if type(record.get("stop_reason")) is not str or not record["stop_reason"]:
            row_issues.append("stop_reason_missing")
        if key[1] == "atomic" and record.get("study_scope") != "full_atomic_public_journey":
            row_issues.append("atomic_is_not_full_public_journey")
        observations.append({"record_index": record_index, "instance_id": key[0], "arm": key[1],
            "success": success, "grade_status": grade_status, "cost_complete": complete_cost and amount(semantic_cost),
            "known_cost_usd": cost, "physical_calls": len(attempts), "tokens_in_known": tokens_in,
            "tokens_out_known": tokens_out, "wall_seconds": wall if amount(wall) else None,
            "human_interventions": interventions if type(interventions) is int and interventions >= 0 else None,
            "false_completion": false_completion, "completion_claimed": claimed, "stop_reason": record.get("stop_reason"),
            "issues": sorted(set(row_issues))})
        issues.extend({"instance_id": key[0], "arm": key[1], "record_index": record_index, "reason": reason}
                      for reason in sorted(set(row_issues)))
    for task_id in expected:
        for arm in ARMS:
            if counts[(task_id, arm)] == 0:
                issues.append({"instance_id": task_id, "arm": arm, "reason": "missing_expected_arm"})
    orders = [task.get("arm_order") for task in tasks]
    if any(order not in (["baseline", "atomic"], ["atomic", "baseline"]) for order in orders):
        issues.append({"reason": "invalid_declared_arm_orders"})
    elif abs(sum(order[0] == "baseline" for order in orders) - sum(order[0] == "atomic" for order in orders)) > 1:
        issues.append({"reason": "declared_order_not_counterbalanced"})
    if any(observation["issues"] for observation in observations):
        statuses = "incomplete"
    else:
        statuses = "measurement_complete" if len(records) == 60 and not issues else "incomplete"
    metrics = {}
    for arm in ARMS:
        arm_rows = [observation for observation in observations if observation["arm"] == arm]
        unique = [r for r in arm_rows if counts[(r["instance_id"], arm)] == 1 and r["instance_id"] in expected]
        successes = sum(row["success"] for row in unique)
        full = len(unique) == 30 and all(not row["issues"] for row in unique)
        cost = sum(row["known_cost_usd"] for row in arm_rows)  # includes failures and duplicates
        walls = [row["wall_seconds"] for row in arm_rows if row["wall_seconds"] is not None]
        metrics[arm] = {"expected_tasks": 30, "observed_records": len(arm_rows), "unique_expected_observations": len(unique),
            "verified_success_count": successes, "verified_success_rate": successes / 30,
            "known_total_cost_usd": cost, "cost_complete": full,
            "complete_cost_per_success_usd": cost / successes if full and successes else None,
            "cost_per_success_status": "complete" if full and successes else "not_assessed_incomplete_or_zero_success",
            "physical_calls_observed": sum(row["physical_calls"] for row in arm_rows),
            "tokens_in_known": sum(row["tokens_in_known"] for row in arm_rows),
            "tokens_out_known": sum(row["tokens_out_known"] for row in arm_rows),
            "wall_observation_count": len(walls), "wall_p50_seconds": percentile(walls, .5),
            "wall_p95_seconds": percentile(walls, .95), "wall_metrics_complete": full and len(walls) == 30,
            "human_interventions_known": sum(row["human_interventions"] or 0 for row in arm_rows),
            "human_interventions_complete": full,
            "false_completion_count": sum(row["false_completion"] is True for row in arm_rows),
            "false_completion_unknown": sum(row["false_completion"] is None for row in arm_rows),
            "stop_reasons": dict(Counter(row["stop_reason"] or "unknown" for row in arm_rows))}
    obligations = obligations or {}
    missing_obligations = []
    for name in PREREQUISITES:
        obligation = obligations.get(name, {})
        if obligation.get("status") != "accepted" or not sha256(obligation.get("evidence_sha256")):
            missing_obligations.append(name)
    signature = digest(frozen)
    for name in ABLATIONS:
        obligation = obligations.get("ablations", {}).get(name, {})
        count = obligation.get("predeclared_task_count")
        if (obligation.get("status") != "complete" or not sha256(obligation.get("evidence_sha256"))
                or obligation.get("frozen_signature_sha256") != signature
                or type(count) is not int or count <= 0 or obligation.get("completed_task_count") != count):
            missing_obligations.append("ablation:" + name)
    paired = Counter()
    for task_id in expected:
        pair = {arm: [row for row in observations if row["instance_id"] == task_id and row["arm"] == arm] for arm in ARMS}
        if any(len(pair[arm]) != 1 or pair[arm][0]["issues"] for arm in ARMS):
            paired["incomplete"] += 1
            continue
        baseline, atomic = pair["baseline"][0]["success"], pair["atomic"][0]["success"]
        paired["both_success" if baseline and atomic else "atomic_only" if atomic else
               "baseline_only" if baseline else "both_failed"] += 1
    paired_result = {key: paired[key] for key in ("both_success", "atomic_only", "baseline_only", "both_failed", "incomplete")}
    paired_result.update(expected_pairs=30, measured_pairs=30 - paired["incomplete"],
        success_rate_difference_atomic_minus_baseline=((paired["atomic_only"] - paired["baseline_only"]) / 30
            if not paired["incomplete"] else None),
        uncertainty="not_assessed; repository clustering, predeclared hypothesis and powered confirmation remain required")
    return {"schema_version": 1, "p07": "OPEN", "status": statuses,
        "closure_readiness": "requires_independent_review" if statuses == "measurement_complete" and not missing_obligations else "incomplete",
        "competitive_advantage": "not_assessed", "expected_task_count": 30, "expected_arm_count": 60,
        "manifest_sha256": digest(manifest), "records_sha256": digest(records), "frozen_signature_sha256": signature,
        "admission": "quarantine" if any(issue["reason"] == "unknown_charge_quarantine" for issue in issues) else "requires_host_budget_admission",
        "all_attempt_receipts_observed": len(all_attempts), "metrics": metrics, "paired_outcomes": paired_result, "issues": issues,
        "missing_obligations": missing_obligations, "observations": observations,
        "limits": "Deterministic accounting over supplied host evidence, not authentication of remote claims or semantic correctness. No automatic P07 closure."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--obligations", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = summarize(json.loads(args.manifest.read_text()), json.loads(args.records.read_text()),
                       json.loads(args.obligations.read_text()) if args.obligations else {})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: report[key] for key in ("p07", "status", "closure_readiness", "admission")}))
    return 0 if report["status"] == "measurement_complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
