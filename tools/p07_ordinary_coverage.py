"""Project retained P07 evidence into a conservative, non-authorizing inventory.

Only task IDs, repositories, base commits and evidence metadata leave the
source files. Hidden patches, issue text, requirements and solutions never enter
the output. This is a snapshot, not an executor admission or a model grade.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = "benchmarks/stage4/corpus-p07-v3.json"
CALIBRATION = "validation/p07-budget20/calibration-inventory.json"
OUTPUT = "benchmarks/stage4/p07-ordinary-coverage-v1.json"
ACCEPTED = {
    "psf__requests-3362": ("validation/p07-budget20/requests3362-full-public-v5/assessment.json", 40818355491,
                           "4d3c08e3eedcc47509ba700a8b8ec9029f335b13", 12872864520),
    "psf__requests-1963": ("validation/p07-budget20/function36-task-v1/psf__requests-1963-assessment.json", 40824311185,
                           "875fd01b1f3f4b4521b887688d27a82678f47df1", 12875842403),
    "psf__requests-2148": ("validation/p07-budget20/function36-task-v1/psf__requests-2148-assessment.json", 40824311185,
                           "875fd01b1f3f4b4521b887688d27a82678f47df1", 12875134258),
    "psf__requests-2317": ("validation/p07-budget20/function36-task-v1/psf__requests-2317-assessment.json", 40824311185,
                           "875fd01b1f3f4b4521b887688d27a82678f47df1", 12876291075),
    "psf__requests-2674": ("validation/p07-budget20/requests2674-task-v1/assessment.json", 40835105105,
                           "520158504af00ab1f08899553458916943ad52b1", 12879763821),
    "psf__requests-863": ("validation/p07-budget20/requests863-task-v1/assessment.json", 40841965525,
                          "3feda8c33b058768f4f1c2348d8b233fdcaf8d37", 12883775725),
}


def read_json(root: Path, name: str):
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("evidence path must be confined")
    raw = (root / relative).read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def build(root: Path):
    corpus, corpus_digest = read_json(root, CORPUS)
    inventory, inventory_digest = read_json(root, CALIBRATION)
    tasks, calibrations = corpus["tasks"], inventory["rows"]
    if len(tasks) != 30 or len(calibrations) != 30:
        raise ValueError("exact frozen 30-task inventory required")
    by_id = {row["instance_id"]: row for row in calibrations}
    if len(by_id) != 30 or len({task["instance_id"] for task in tasks}) != 30:
        raise ValueError("duplicate task or calibration identity")
    result = []
    for task in tasks:
        task_id, repo, base = (task[key] for key in ("instance_id", "repo", "base_commit"))
        if (not re.fullmatch(r"[A-Za-z0-9_-]+", task_id) or not re.fullmatch(r"[a-f0-9]{40}", base)
                or task_id not in by_id):
            raise ValueError("invalid task or base identity")
        row = by_id[task_id]
        if row["base_commit"] != base or row["status"] != "accepted":
            raise ValueError("calibration registry does not bind exact base")
        evidence, digest = read_json(root, row["accepted_evidence"])
        if evidence.get("instance_id") != task_id or evidence.get("calibrated") is not True:
            raise ValueError("calibration report does not match task")
        report_base = evidence.get("base_commit")
        if report_base is None:
            rows = evidence.get("rows", [])
            if len(rows) != 3 or any(item.get("base_verified") != base for item in rows):
                raise ValueError("calibration report lacks exact base verification")
        elif report_base != base:
            raise ValueError("calibration base differs")
        entry = {"instance_id": task_id, "repo": repo, "base_commit": base,
                 "calibration": {"status": "accepted_in_retained_inventory", "report": row["accepted_evidence"],
                                 "report_sha256": digest},
                 "full_public_ordinary": {"status": "not_established_in_this_inventory"}}
        if task_id in ACCEPTED:
            name, run, commit, artifact = ACCEPTED[task_id]
            assessment, assessment_digest = read_json(root, name)
            reported_artifact = assessment.get("artifact", assessment.get("archive"))
            if isinstance(reported_artifact, dict):
                reported_artifact = reported_artifact.get("id")
            if (assessment.get("provider_calls") != 0 or assessment.get("hidden_oracle") != "not_run"
                    or assessment.get("base_commit", assessment.get("public_base_commit", base)) != base
                    or reported_artifact != artifact
                    or assessment.get("run", assessment.get("run_id", run)) != run
                    or assessment.get("tested_commit", commit) != commit):
                raise ValueError("ordinary evidence scope or base differs")
            entry["full_public_ordinary"] = {"status": "accepted_scripted_synthetic_repair_scope",
                "assessment": name, "assessment_sha256": assessment_digest, "workflow_run": run,
                "tested_commit": commit, "artifact_id": artifact}
        result.append(entry)
    if not set(ACCEPTED) <= set(by_id):
        raise ValueError("accepted ordinary evidence references unknown task")
    return {"schema_version": 1, "scope": "conservative evidence index; no runtime admission or model quality claim",
            "corpus_sha256": corpus_digest, "calibration_inventory_sha256": inventory_digest,
            "counts": {"tasks": len(result), "retained_calibrations": len(result),
                       "accepted_scripted_ordinary": len(ACCEPTED), "ordinary_not_established": len(result) - len(ACCEPTED)},
            "tasks": result}


def main():
    value = build(ROOT)
    (ROOT / OUTPUT).write_bytes((json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("ascii"))
    print(json.dumps(value["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
