"""Audit saved pilot JSON and hashes offline; never imports or runs candidates.

Only immediate stage4-live-pilot-*/report.json, attempts.json, kaggle-quota.json
and optional candidate.patch are read. Missing receipts remain evidence gaps;
repeated request/response hashes are retained and never treated as duplicate sends.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

MAX_FILE_BYTES = 8_000_000


def amount(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def count(value):
    return type(value) is int and value >= 0


def audit(root, *, tolerance_usd=1e-9):
    """Return evidence observations, not provider billing or model quality claims."""
    if not amount(tolerance_usd) or tolerance_usd == 0:
        raise ValueError("positive finite comparison tolerance required")
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("pilot evidence root must be a directory")
    result = {"version": 1, "audit_kind": "offline_saved_pilot_evidence",
              "generated_at_utc": datetime.now(UTC).isoformat(),
              "root": str(root), "tolerance_usd": tolerance_usd,
              "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "model_calls_made_by_audit": 0, "candidate_execution": False,
              "model_quality_measured": False, "runs": [], "evidence_files": [],
              "inconsistencies": [], "warnings": []}
    fingerprints = Counter()

    def issue(run, code, *, warning=False, **details):
        result["warnings" if warning else "inconsistencies"].append({"run": run, "code": code, **details})

    def read(path, run, *, optional=False, raw=False):
        if not path.exists():
            if not optional:
                issue(run, "missing_" + path.name.replace(".", "_"), warning=True)
            return None
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("evidence path leaves root or is a symlink")
            with path.open("rb") as stream:
                body = stream.read(MAX_FILE_BYTES + 1)
            if len(body) > MAX_FILE_BYTES:
                raise ValueError("evidence file exceeds byte bound")
            result["evidence_files"].append({"path": path.relative_to(root).as_posix(),
                                              "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()})
            return body if raw else json.loads(body)
        except (OSError, ValueError, UnicodeError):
            issue(run, "unreadable_or_invalid_" + path.name.replace(".", "_"))
            return None

    for report_path in sorted(root.glob("stage4-live-pilot-*/report.json")):
        directory = report_path.parent
        name = directory.name
        if directory.is_symlink() or not directory.resolve().is_relative_to(root):
            issue(name, "run_directory_leaves_root_or_is_symlink")
            continue
        report = read(directory / "report.json", name)
        if report is None:
            continue
        if not isinstance(report, dict):
            issue(name, "report_not_object")
            continue
        arm, calls, cost = report.get("arm"), report.get("calls"), report.get("cost_usd")
        complete = report.get("cost_complete")
        valid_arm = isinstance(arm, str) and arm in {"baseline", "atomic"}
        if not valid_arm:
            issue(name, "invalid_arm")
        if not count(calls):
            issue(name, "invalid_report_call_count")
        if cost is not None and not amount(cost):
            issue(name, "invalid_report_cost")
        if type(complete) is not bool or complete != (cost is not None):
            issue(name, "report_cost_completeness_mismatch")
        labels = {}
        for key in ("status", "task_status", "error_type"):
            value = report.get(key)
            if value is not None and not isinstance(value, str):
                issue(name, "invalid_" + key)
            labels[key] = value if isinstance(value, str) else None
        wall = report.get("wall_seconds")
        if wall is not None and not amount(wall):
            issue(name, "invalid_wall_seconds")
        row = {"run": name, "arm": arm if valid_arm else "invalid",
               **labels, "calls_reported": calls if count(calls) else None,
               "cost_reported_usd": cost if amount(cost) else None, "cost_complete": complete is True,
               "wall_seconds": wall if amount(wall) else None, "attempt_records": None,
               "unknown_charge_records": None, "known_cost_lower_bound_usd": 0.0,
               "quota_receipt": False, "quota_comparisons": []}
        rows = read(directory / "attempts.json", name)
        if rows is not None and (not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows)):
            issue(name, "attempts_not_object_list")
            rows = None
        if rows is not None:
            row["attempt_records"] = len(rows)
            if count(calls) and calls != len(rows):
                issue(name, "call_count_mismatch", reported=calls, observed=len(rows))
            indices = [r.get("attempt") for r in rows]
            if any(not count(i) or i == 0 for i in indices) or len(set(i for i in indices if count(i))) != len(rows):
                issue(name, "invalid_or_duplicate_attempt_index")
            invalid_costs = [i + 1 for i, r in enumerate(rows) if r.get("cost_usd") is not None and not amount(r["cost_usd"])]
            if invalid_costs:
                issue(name, "invalid_attempt_cost", attempts=invalid_costs)
            unknown = sum(r.get("cost_usd") is None for r in rows)
            known = math.fsum(r["cost_usd"] for r in rows if amount(r.get("cost_usd")))
            row.update(known_cost_lower_bound_usd=known, unknown_charge_records=unknown,
                       attempt_status_counts=dict(Counter(str(r.get("status")) for r in rows)),
                       request_outcome_counts=dict(Counter(str(r.get("request_outcome")) for r in rows)))
            rows_complete = bool(rows) and not unknown and not invalid_costs
            if complete != rows_complete:
                issue(name, "attempt_cost_completeness_mismatch", report_complete=complete,
                      attempt_costs_complete=rows_complete)
            if complete and amount(cost) and abs(cost - known) > tolerance_usd:
                issue(name, "report_attempt_cost_mismatch", reported=cost, observed=known)
            lower = report.get("known_cost_lower_bound_usd")
            if lower is not None and (not amount(lower) or abs(lower - known) > tolerance_usd):
                issue(name, "known_cost_lower_bound_mismatch")
            for token in ("tokens_in", "tokens_out"):
                values = [r.get(token) for r in rows]
                if values and all(count(v) for v in values) and count(report.get(token)) and sum(values) != report[token]:
                    issue(name, token + "_mismatch", reported=report[token], observed=sum(values))
            for attempt in rows:
                pair = (attempt.get("request_sha256"), attempt.get("response_sha256"))
                if all(isinstance(value, str) and value for value in pair):
                    fingerprints[pair] += 1
        else:
            lower = report.get("known_cost_lower_bound_usd", cost)
            row["known_cost_lower_bound_usd"] = lower if amount(lower) else 0.0
        candidate = read(directory / "candidate.patch", name, optional=True, raw=True)
        if candidate is not None:
            candidate_hash = hashlib.sha256(candidate).hexdigest()
            row["candidate_sha256"] = candidate_hash
            expected = report.get("patch_sha256")
            if expected is not None and not isinstance(expected, str):
                issue(name, "invalid_report_candidate_hash")
            elif expected is not None and expected != candidate_hash:
                issue(name, "candidate_hash_mismatch", reported=expected, observed=candidate_hash)
        elif report.get("patch_sha256"):
            issue(name, "candidate_file_missing", warning=True)
        receipt = read(directory / "kaggle-quota.json", name)
        if receipt is not None:
            if not isinstance(receipt, dict):
                issue(name, "quota_not_object")
            else:
                row["quota_receipt"] = True
                row["credential_label"] = receipt.get("credential_label") if isinstance(receipt.get("credential_label"), str) else None
                row["quota_utc"] = receipt.get("utc") if isinstance(receipt.get("utc"), str) else None
                sides = {}
                for side in ("before", "after"):
                    balances = receipt.get(side)
                    if not isinstance(balances, list) or not balances or any(not isinstance(b, dict) or not isinstance(b.get("period"), str)
                                                            or not amount(b.get("used")) or not amount(b.get("allowed")) for b in balances):
                        issue(name, "invalid_quota_" + side)
                        sides[side] = {}
                        continue
                    sides[side] = {b["period"]: b for b in balances}
                    if len(sides[side]) != len(balances):
                        issue(name, "duplicate_quota_period_" + side)
                if sides["before"].keys() != sides["after"].keys():
                    issue(name, "quota_periods_mismatch")
                for period in sorted(sides["before"].keys() & sides["after"].keys()):
                    before, after = sides["before"][period], sides["after"][period]
                    delta = after["used"] - before["used"]
                    comparison = {"period": period, "before_used": before["used"], "after_used": after["used"],
                                  "allowed_after": after["allowed"], "delta_usd": delta, "matches_reported_cost": None}
                    if delta < -tolerance_usd:
                        issue(name, "quota_counter_decreased_possible_reset", warning=True, period=period)
                    elif amount(cost) and complete:
                        comparison["absolute_error_usd"] = abs(delta - cost)
                        comparison["matches_reported_cost"] = abs(delta - cost) <= tolerance_usd
                        if not comparison["matches_reported_cost"]:
                            issue(name, "quota_cost_mismatch", period=period, reported=cost, observed_delta=delta)
                    row["quota_comparisons"].append(comparison)
        result["runs"].append(row)

    by_arm = {}
    for arm in sorted({row["arm"] for row in result["runs"]}):
        selected = [row for row in result["runs"] if row["arm"] == arm]
        by_arm[arm] = {"runs": len(selected), "calls_reported": sum(r["calls_reported"] or 0 for r in selected),
                       "attempt_records": sum(r["attempt_records"] or 0 for r in selected),
                       "zero_call_runs": sum(r["calls_reported"] == 0 for r in selected),
                       "incomplete_cost_runs": sum(not r["cost_complete"] for r in selected),
                       "known_cost_lower_bound_usd": math.fsum(r["known_cost_lower_bound_usd"] for r in selected),
                       "status_counts": dict(Counter(str(r["status"]) for r in selected))}
    known_total = math.fsum(r["known_cost_lower_bound_usd"] for r in result["runs"])
    missing_receipts = [r["run"] for r in result["runs"] if not r["quota_receipt"]]
    errors = [q["absolute_error_usd"] for r in result["runs"] for q in r["quota_comparisons"] if "absolute_error_usd" in q]
    result.update(by_arm=by_arm, summary={"runs": len(result["runs"]),
                  "calls_reported": sum(r["calls_reported"] or 0 for r in result["runs"]),
                  "attempt_records": sum(r["attempt_records"] or 0 for r in result["runs"]),
                  "known_cost_lower_bound_usd": known_total,
                  "incomplete_cost_runs": sum(not r["cost_complete"] for r in result["runs"]),
                  "zero_call_runs": sum(r["calls_reported"] == 0 for r in result["runs"]),
                  "inconsistencies": len(result["inconsistencies"]), "warnings": len(result["warnings"])},
                  quota_coverage={"receipts": len(result["runs"]) - len(missing_receipts),
                                  "missing_runs": missing_receipts,
                                  "missing_charged_runs": [r["run"] for r in result["runs"] if not r["quota_receipt"] and (r["calls_reported"] or 0) > 0],
                                  "all_runs_have_receipts": not missing_receipts,
                                  "max_observed_absolute_error_usd": max(errors) if errors else None},
                  repeated_fingerprints=[{"request_sha256": pair[0], "response_sha256": pair[1], "records": n}
                                         for pair, n in sorted(fingerprints.items()) if n > 1],
                  interpretation="Saved records are retained without deduplication. Missing receipts and unknown charges are not known zero. A quota match does not prove absence of other account activity or provider billing guarantees.")
    if not result["runs"]:
        issue(None, "no_pilot_reports")
        result["summary"]["inconsistencies"] = len(result["inconsistencies"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tolerance-usd", type=float, default=1e-9)
    args = parser.parse_args()
    result = audit(args.root, tolerance_usd=args.tolerance_usd)
    destination = args.output.resolve()
    relative = destination.relative_to(Path(result["root"])) if destination.is_relative_to(Path(result["root"])) else None
    if (relative is not None and len(relative.parts) == 2 and relative.parts[0].startswith("stage4-live-pilot-")
            and relative.name in {"report.json", "attempts.json", "kaggle-quota.json", "candidate.patch"}):
        parser.error("audit output cannot overwrite historical pilot evidence")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"]))
    return 1 if result["inconsistencies"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
