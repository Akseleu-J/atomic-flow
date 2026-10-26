"""Prospective oracle controls preserve corpus and source-only containment."""
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "benchmarks/stage4"
CHANGED = {
    "pytest-dev__pytest-5227",
    "pytest-dev__pytest-7432",
    "pytest-dev__pytest-11148",
    "pytest-dev__pytest-11143",
    "sympy__sympy-17139",
}


def git_lf_hash(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load(name):
    return json.loads((BASE / name).read_text(encoding="utf-8"))


def test_migration_pins_predecessor_and_unchanged_corpus():
    v3 = load("p07-controls-v3.json")
    assert v3["version"] == 3
    assert v3["status"] == "prospective_ungraded"
    assert v3["previous_controls_git_lf_sha256"] == git_lf_hash(BASE / "p07-controls-v2.json")
    assert v3["previous_controls_git_lf_sha256"] == "b7f2df4bda63befd1736f55191c2fdade5ce0c9eed8ef0f7a9020bd09ac86ea0"
    assert git_lf_hash(BASE / "corpus-p07-v2.json") == "ac32cd32aa0305821b440b9de277f0b3e3d5c1c55c9b485091f96b19773713dd"
    assert v3["calibration_evidence_workflow"] == 40309674235
    assert set(v3["changed_tasks"]) == CHANGED
    assert v3["model_visible"] is False


def test_only_predeclared_five_controls_change():
    before = load("p07-controls-v2.json")["tasks"]
    after = load("p07-controls-v3.json")["tasks"]
    assert set(before) == set(after)
    assert {task for task in before if before[task] != after[task]} == CHANGED
    for task in before:
        assert before[task]["gold_sha256"] == after[task]["gold_sha256"]


@pytest.mark.parametrize("task_id", sorted(CHANGED))
def test_wrong_mutation_preserves_patch_scope_and_oracle_boundaries(task_id):
    task = next(task for task in load("corpus-p07-v2.json")["tasks"] if task["instance_id"] == task_id)
    control = load("p07-controls-v3.json")["tasks"][task_id]
    patch = task["patch"]
    assert hashlib.sha256(patch.encode()).hexdigest() == control["gold_sha256"]
    assert patch.count(control["old"]) == 1
    assert control["old"].startswith("+") and not control["old"].startswith("+++")
    assert control["new"].startswith("+") and not control["new"].startswith("+++")
    assert "\n" not in control["old"] and "\n" not in control["new"]
    wrong = patch.replace(control["old"], control["new"])
    assert wrong != patch
    assert [line for line in patch.splitlines() if not line.startswith("+")] == [
        line for line in wrong.splitlines() if not line.startswith("+")
    ]
    assert [line for line in patch.splitlines() if line.startswith("+++ ")] == [
        line for line in wrong.splitlines() if line.startswith("+++ ")
    ]
    for marker in ("testing/", "tests/", "test_", "PYTEST_CURRENT_TEST"):
        assert marker not in control["new"]
    assert control["rationale"]
