"""Prospective controls cannot silently rewrite predecessor or oracle boundaries."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_p07_controls_v4", ROOT / "tools/prepare_p07_controls_v4.py")
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_materialization_binds_exact_predecessor_and_changes_only_three(tmp_path):
    output = tmp_path / "controls-v4.json"
    before = (prepare.BASE / "p07-controls-v3.json").read_bytes()
    result = prepare.materialize(output)
    previous = json.loads(before)
    assert result["version"] == 4 and result["status"] == "prospective_ungraded"
    assert result["calibration_result"] == "not_run" and result["model_visible"] is False
    assert result["previous_controls_git_lf_sha256"] == prepare.lf_hash(prepare.BASE / "p07-controls-v3.json")
    assert result["corpus_git_lf_sha256"] == "ac32cd32aa0305821b440b9de277f0b3e3d5c1c55c9b485091f96b19773713dd"
    assert {task for task in previous["tasks"] if previous["tasks"][task] != result["tasks"][task]} == set(prepare.CHANGES)
    assert (prepare.BASE / "p07-controls-v3.json").read_bytes() == before
    assert load(output) == result
    with pytest.raises(ValueError, match="fresh"):
        prepare.materialize(output)


@pytest.mark.parametrize("task_id", sorted(prepare.CHANGES))
def test_unique_source_line_change_preserves_hunks_paths_and_gold_identity(task_id):
    tasks = {task["instance_id"]: task for task in load(prepare.BASE / "corpus-p07-v2.json")["tasks"]}
    gold = tasks[task_id]["patch"]
    control = prepare.CHANGES[task_id]
    assert gold.count(control["old"]) == 1
    assert control["old"].startswith("+") and control["new"].startswith("+")
    assert "\n" not in control["old"] and "\n" not in control["new"]
    wrong = gold.replace(control["old"], control["new"])
    assert [line for line in gold.splitlines() if not line.startswith("+")] == [
        line for line in wrong.splitlines() if not line.startswith("+")]
    assert [line for line in gold.splitlines() if line.startswith("+++ ")] == [
        line for line in wrong.splitlines() if line.startswith("+++ ")]
    assert all(marker not in control["new"] for marker in ("test_", "tests/", "PYTEST_CURRENT_TEST"))


def test_stale_gold_or_changed_source_is_refused_before_output(tmp_path):
    corpus = load(prepare.BASE / "corpus-p07-v2.json")
    next(task for task in corpus["tasks"] if task["instance_id"] in prepare.CHANGES)["patch"] += "\n"
    changed = tmp_path / "changed-corpus.json"
    changed.write_text(json.dumps(corpus))
    output = tmp_path / "out.json"
    with pytest.raises(ValueError, match="exact reviewed gold"):
        prepare.materialize(output, corpus=changed)
    assert not output.exists()
