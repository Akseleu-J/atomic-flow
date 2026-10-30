"""Curate a bounded public SWE-bench Lite snapshot; no evaluator scripts execute."""
import argparse
import hashlib
import json
from pathlib import Path

REPOS = {"psf/requests": "development", "pytest-dev/pytest": "development",
         "pylint-dev/pylint": "confirmation", "sphinx-doc/sphinx": "confirmation", "sympy/sympy": "confirmation"}


def build(pages, revision):
    raw_pages = [Path(p).read_bytes() for p in pages]
    rows = [item["row"] for raw in raw_pages for item in json.loads(raw)["rows"]]
    tasks = []
    for repo, split in REPOS.items():
        candidates = sorted((r for r in rows if r["repo"] == repo), key=lambda r: (len(r["patch"].encode()), r["instance_id"]))
        if len(candidates) < 6:
            raise ValueError("insufficient independent repository tasks")
        for row in candidates[:6]:
            task = {key: row[key] for key in ("instance_id", "repo", "base_commit", "problem_statement", "patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS", "version", "created_at")}
            task.update(split=split, calibration_status="not_run", environment_status="not_prepared",
                        issue_pr_url="https://github.com/" + repo + "/pull/" + row["instance_id"].rsplit("-", 1)[1])
            for key in ("patch", "test_patch"):
                task[key + "_sha256"] = hashlib.sha256(task[key].encode()).hexdigest()
            task["upstream_row_sha256"] = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            tasks.append(task)
    return {"version": 1, "dataset": "SWE-bench/SWE-bench_Lite", "dataset_revision_observed": revision,
            "source": "https://huggingface.co/datasets/SWE-bench/SWE-bench_Lite/tree/" + revision,
            "rows_source": "https://datasets-server.huggingface.co/rows?dataset=SWE-bench%2FSWE-bench_Lite&config=default&split=test",
            "provenance_note": "Rows API is not commit-addressed; revision observed before/after export; page/row/patch hashes pin exported bytes independently.",
            "source_page_sha256": [hashlib.sha256(raw).hexdigest() for raw in raw_pages],
            "selection": "Six smallest UTF-8 gold patches per named repository, tie break instance id; repo split; no success-based selection",
            "model_quality_measured": False, "tasks": tasks}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pages", nargs="+")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(build(args.pages, args.revision), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
