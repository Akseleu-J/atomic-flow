"""Prospective metadata repair must preserve tasks, source and protected tests."""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "benchmarks/stage4"


def test_versioned_labels_preserve_all_task_code_and_match_audited_hashes():
    migration = json.loads((ROOT / "p07-manifest-migrations.json").read_text())
    old_raw, new_raw = (ROOT / "corpus.json").read_bytes(), (ROOT / "corpus-p07-v2.json").read_bytes()
    assert migration["hash_contract"] == "git_text_lf_bytes"
    assert hashlib.sha256(old_raw.replace(b"\r\n", b"\n")).hexdigest() == migration["original_sha256"]
    assert hashlib.sha256(new_raw.replace(b"\r\n", b"\n")).hexdigest() == migration["new_sha256"]
    old, new = json.loads(old_raw), json.loads(new_raw)
    assert len(old["tasks"]) == len(new["tasks"]) == 30
    permitted = {"pytest-dev__pytest-5227", "sympy__sympy-18057"}
    for before, after in zip(old["tasks"], new["tasks"], strict=True):
        assert before["instance_id"] == after["instance_id"]
        if before["instance_id"] not in permitted:
            assert before == after
        else:
            assert {k: v for k, v in before.items() if k not in {"FAIL_TO_PASS", "PASS_TO_PASS"}} == {
                k: v for k, v in after.items() if k not in {"FAIL_TO_PASS", "PASS_TO_PASS"}}
    fixed = next(t for t in new["tasks"] if t["instance_id"] == "pytest-dev__pytest-5227")
    assert all(name not in fixed["PASS_TO_PASS"] for name in ["[100%]", "[", "[100%]------------------------------"])
    sympy = next(t for t in new["tasks"] if t["instance_id"] == "sympy__sympy-18057")
    assert set(sympy["FAIL_TO_PASS"]) == {"test_var", "test_non_string_equality"}


def test_versioned_negative_controls_bind_to_same_gold_and_only_two_changed_cases():
    tasks = json.loads((ROOT / "corpus.json").read_text())["tasks"]
    old = json.loads((ROOT / "p07-controls.json").read_text())["tasks"]
    new = json.loads((ROOT / "p07-controls-v2.json").read_text())["tasks"]
    assert set(old) == set(new) == {t["instance_id"] for t in tasks}
    changed = {name for name in old if old[name] != new[name]}
    assert changed == {"pylint-dev__pylint-5859", "pylint-dev__pylint-7993"}
    for task in tasks:
        control = new[task["instance_id"]]
        assert hashlib.sha256(task["patch"].encode()).hexdigest() == control["gold_sha256"]
        assert task["patch"].count(control["old"]) == 1
        assert control["old"] != control["new"] and control["rationale"]
