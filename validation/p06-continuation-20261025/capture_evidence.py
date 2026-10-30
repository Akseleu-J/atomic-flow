"""Capture local evidence identities, without running providers or tests again."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
FILES = [
    "src/atomic_flow/agents/envelope.py", "src/atomic_flow/agents/plan_review.py",
    "src/atomic_flow/agents/task.py", "src/atomic_flow/agents/task_loop.py", "src/atomic_flow/cli.py",
    "src/atomic_flow/graph/store.py", "src/atomic_flow/graph/delta.py",
    "tests/test_envelope.py", "tests/test_internal_replan.py", "tests/test_graph_cas_review.py",
    "tests/test_step_invalidation.py", "tests/test_replan_cli.py",
]
NEIGHBORS = ["tests/test_task.py", "tests/test_task_changeset.py", "tests/test_task_budget.py",
             "tests/test_cli.py", "tests/test_execution.py", "tests/test_completion_diagnostics.py",
             "tests/test_task_execution.py", "tests/test_composition_acceptance.py"]
FOCUSED = ["tests/test_envelope.py", "tests/test_internal_replan.py", "tests/test_plan_review_dependencies.py",
           "tests/test_step_invalidation.py", "tests/test_task_read_plan.py", "tests/test_graph_cas_review.py",
           "tests/test_graph_contract.py", "tests/test_graph_delta.py", "tests/test_graph_resolve.py",
           "tests/test_graph_semantic.py", "tests/test_graph_impact.py"]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    # Neighbor stdout was delivered by the tool, not redirected by its command.
    captured = {"capture": "write_stdin session 79430 final tool output",
                "exit_code": 0, "passed": 85, "skipped": 2, "seconds": 153.81,
                "files": NEIGHBORS,
                "stdout_summary": "85 passed, 2 skipped in 153.81s (0:02:33)",
                "not_a_raw_stdout_file": True}
    (OUT / "neighbors-result.json").write_text(json.dumps(captured, indent=2) + "\n", encoding="utf-8")
    diff = subprocess.check_output(["git", "diff", "HEAD", "--binary", "--", "src", "tests"], cwd=ROOT)
    (OUT / "tracked-runtime.diff").write_bytes(diff)
    sources = {name: sha((ROOT / name).read_bytes()) for name in FILES}
    manifest = {
        "date": "2026-10-25", "timezone": "Europe/Moscow",
        "starting_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
        "python": sys.version, "scope": "local uncommitted package; offline/scripted verification only",
        "live_provider_calls": 0, "vault_access": False,
        "checks": {
            "focused": {"files": FOCUSED, "passed": 79, "skipped": 0, "seconds": 38.39,
                        "log": "pytest-focused.txt", "exit_code": 0},
            "neighbors": captured,
            "new_cli": {"files": ["tests/test_replan_cli.py"], "passed": 4, "seconds": 1.06,
                        "log": "pytest-cli.txt", "exit_code": 0},
            "total_disjoint_final_selections": {"passed": 168, "skipped": 2},
            "ruff": "passed", "compileall": "passed", "git_diff_check": "passed",
        },
        "source_sha256": sources,
        "tracked_runtime_diff_sha256": sha(diff),
        "untracked_test_bytes_bound_by_source_sha256": [name for name in FILES if name in
            {"tests/test_internal_replan.py", "tests/test_graph_cas_review.py", "tests/test_replan_cli.py"}],
        "ci_scope": {"source_commit": "20cdd7767ad846ad42dd9d08fcd709c4d5c11b0d",
                     "checks_run": 40159998948, "external_run": 40159998952,
                     "status": "success for starting commit, not this working diff"},
        "limitations": [
            "Initial ordinary sandbox shell processes stalled; node child spawn returned EPERM; approved local shell used.",
            "Initial neighbor test selection included nonexistent test_state.py and ran no tests; corrected selection recorded.",
            "Earlier overlapping development selections are excluded from totals.",
            "Two skips remain unexecuted coverage; this is not full CI, live model quality or remote executor acceptance.",
            "Existing dirty docs/project index and untracked tree.lock/reference/review artifacts were preserved.",
            "No new commit or push was performed.",
        ],
    }
    for name in ("pytest-focused.txt", "pytest-cli.txt", "neighbors-result.json", "capture_evidence.py"):
        manifest.setdefault("artifact_sha256", {})[name] = sha((OUT / name).read_bytes())
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"bound_source_files": len(sources), "passed": 168, "skipped": 2,
                      "live_provider_calls": 0, "ci_for_working_diff": False}))


if __name__ == "__main__":
    main()
