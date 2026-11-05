"""Synthetic oracle calibration tests; not a model or public-corpus quality score."""
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("stage4", ROOT / "tools/evaluation_stage4.py")
stage4 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage4)

sys.modules.setdefault("evaluation_stage4", stage4)
PROVENANCE_SPEC = importlib.util.spec_from_file_location("evaluation_provenance", ROOT / "tools/evaluation_provenance.py")
provenance = importlib.util.module_from_spec(PROVENANCE_SPEC)
PROVENANCE_SPEC.loader.exec_module(provenance)
sys.modules.setdefault("evaluation_provenance", provenance)
PILOT_SPEC = importlib.util.spec_from_file_location("live_pilot", ROOT / "tools/evaluation_live_pilot.py")
live_pilot = importlib.util.module_from_spec(PILOT_SPEC)
PILOT_SPEC.loader.exec_module(live_pilot)


DIFF = "--- a/sympy/core/function.py\n+++ b/sympy/core/function.py\n@@ -1 +1 @@\n-x\n+y\n"


def test_pilot_patch_parser_accepts_json_and_diff_fences():
    fenced_json = "```json\n" + json.dumps({"patch": DIFF}) + "\n```"
    fenced_diff = "```diff\n" + DIFF + "```"
    fenced_plain = "```\n" + DIFF + "```"
    assert live_pilot.parse_patch(fenced_json) == DIFF
    assert live_pilot.parse_patch(fenced_diff) == DIFF
    assert live_pilot.parse_patch(fenced_plain) == DIFF
    assert live_pilot.parse_patch(json.dumps({"patch": DIFF})) == DIFF


def test_pilot_patch_parser_still_refuses_wrong_scope_and_shape():
    with pytest.raises(ValueError):
        live_pilot.parse_patch("```json\n" + json.dumps({"patch": "--- a/other.py\n+++ b/other.py\n"}) + "\n```")
    with pytest.raises(ValueError):
        live_pilot.parse_patch("```json\n" + json.dumps({"patch": DIFF, "extra": 1}) + "\n```")


def test_public_corpus_has_independent_thirty_frozen_tasks():
    manifest = stage4.load_manifest(ROOT / "benchmarks/stage4/corpus.json")
    assert len(manifest["tasks"]) == 30
    assert len({t["repo"] for t in manifest["tasks"]}) == 5
    assert not manifest["model_quality_measured"]
    assert all(t["calibration_status"] == "not_run" for t in manifest["tasks"])
    assert not any(t["repo"] in {r["repo"] for r in manifest["tasks"] if r["split"] == "confirmation"}
                   for t in manifest["tasks"] if t["split"] == "development")


@pytest.mark.parametrize("text", ["--- a/../secret\n+++ b/../secret\n", "--- a/.git/config\n+++ b/.git/config\n", "x", "new file mode 120000\n--- a/x\n+++ b/x\n"])
def test_unsafe_patch_paths_refused(text):
    with pytest.raises(ValueError):
        stage4.patch_paths(text)


def test_real_git_patch_calibration_base_gold_wrong(tmp_path):
    source = tmp_path / "source"
    source.mkdir()

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=source, text=True).strip()

    git("init")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Fixture")
    (source / "calc.py").write_text("def answer():\n    return 1\n")
    git("add", "calc.py")
    git("commit", "-m", "synthetic base")
    base = git("rev-parse", "HEAD")
    gold = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def answer():\n-    return 1\n+    return 2\n"
    oracle = "diff --git a/test_requirement.py b/test_requirement.py\nnew file mode 100644\n--- /dev/null\n+++ b/test_requirement.py\n@@ -0,0 +1,3 @@\n+from calc import answer\n+def test_answer():\n+    assert answer() == 2\n"
    task = {"instance_id": "fixture-1", "base_commit": base, "test_patch": oracle, "patch": gold,
            "FAIL_TO_PASS": ["test_requirement.py::test_answer"], "PASS_TO_PASS": []}
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps(["test_requirement.py"]))
    command = [sys.executable, "-I", str(ROOT / "tools/evaluation_pytest.py"), "--workspace", "{workspace}",
               "--report", "{report}", "--tests", str(targets)]
    result = stage4.calibrate(task, source, command, tmp_path / "reports", gold.replace("+    return 2", "+    return 3"))
    assert result["calibrated"], result
    assert [r["status"] for r in result["rows"]] == ["requirement_failed", "verified_success", "requirement_failed"]
    assert not result["model_quality_measured"]
    forbidden = stage4.grade(task, source, oracle, command, tmp_path / "forbidden")
    assert forbidden["status"] == "infrastructure_error" and not forbidden["verified_success"]


def test_checked_git_checkouts_are_byte_identical_to_blobs(tmp_path):
    # Git for Windows defaults to core.autocrlf=true, which rewrites LF blobs
    # to CRLF on disk; a host with that default must still get exact bytes.
    origin = tmp_path / "origin"
    origin.mkdir()
    stage4.checked_git(["init"], origin)
    stage4.checked_git(["config", "user.email", "fixture@example.invalid"], origin)
    stage4.checked_git(["config", "user.name", "Fixture"], origin)
    body = b"def answer():\n    return 1\n"
    (origin / "calc.py").write_bytes(body)
    stage4.checked_git(["add", "calc.py"], origin)
    stage4.checked_git(["commit", "-m", "base"], origin)
    base = stage4.checked_git(["rev-parse", "HEAD"], origin)
    clone = tmp_path / "clone"
    stage4.checked_git(["clone", "--no-checkout", str(origin), str(clone)])
    stage4.checked_git(["checkout", "--detach", base], clone)
    blob = stage4.checked_git(["show", base + ":calc.py"], clone).encode() + b"\n"
    assert (clone / "calc.py").read_bytes() == blob


def attempt(arm="atomic", cost=None):
    return {"instance_id": "task1", "arm": arm, "limits": {"calls": 10}, "model": "fixed", "provider": "fixed",
            "base_commit": "a" * 40, "calls": 1, "tokens_in": 10, "tokens_out": 5, "human_interventions": 0,
            "wall_seconds": 1.0, "cost_usd": cost, "cost_complete": cost is not None, "status": "failed"}


def test_missing_failed_unknown_attempts_are_not_dropped_or_zeroed():
    tasks = [{"instance_id": "task1", "split": "development", "base_commit": "a" * 40}]
    report = stage4.pair_report(tasks, [attempt()], {"calls": 10})
    assert report["missing_attempts"] == report["unknown_cost_attempts"] == 1
    assert report["pairs"][0]["attempts"]["atomic"]["cost_usd"] is None
    assert report["pairs"][0]["status"] == "incomplete" and not report["model_quality_measured"]


def test_pair_identity_and_unknown_cost_cannot_be_forged():
    row = attempt()
    row["cost_complete"] = True
    with pytest.raises(ValueError):
        stage4.validate_attempt(row, {"calls": 10})
    row = attempt()
    other = copy.deepcopy(row)
    other.update(arm="baseline", model="different")
    tasks = [{"instance_id": "task1", "split": "development", "base_commit": "a" * 40}]
    with pytest.raises(ValueError):
        stage4.pair_report(tasks, [row, other], {"calls": 10})


def test_prepare_never_exports_gold_oracle_into_model_prompt(tmp_path):
    manifest = stage4.load_manifest(ROOT / "benchmarks/stage4/corpus.json")
    prepared = stage4.prepare(manifest, tmp_path, {"max_calls": 10})
    assert len(prepared["schedule"]) == 30 and not prepared["model_quality_measured"]
    sample = manifest["tasks"][0]
    prompt = json.loads((tmp_path / (sample["instance_id"] + "-prompt.json")).read_text())
    assert not {"patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS", "hints_text"} & prompt.keys()
    assert prompt["base_commit"] == sample["base_commit"]


def test_empty_score_includes_all_tasks_and_has_no_quality_claim():
    tasks = [{"instance_id": "task1", "split": "development", "base_commit": "a" * 40}]
    result = stage4.score_pairs(tasks, [], {"calls": 10}, [], [])
    assert result["missing_attempts"] == 2
    assert result["totals"]["atomic"]["all_task_denominator"] == 1
    assert result["totals"]["atomic"]["cost_per_success_usd"] is None
    assert not result["model_quality_measured"]
    row = attempt()
    row["tokens_in"] = row["tokens_out"] = None
    stage4.validate_attempt(row, {"calls": 10})
