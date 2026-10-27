"""Prospective3362 bytes-fallback control; host syntax checks, no target execution."""
import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "benchmarks/stage4"
TASK = "psf__requests-3362"


def load(name):
    return json.loads((BASE / name).read_text(encoding="utf-8"))


def lf_hash(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_v6_freezes_predecessor_corpus_and_prospective_scope():
    controls = load("p07-controls-v6.json")
    assert controls["version"] == 6 and controls["status"] == "prospective_ungraded"
    assert controls["calibration_result"] == "not_run" and controls["model_visible"] is False
    assert controls["changed_tasks"] == [TASK]
    assert controls["previous_controls_file"] == "benchmarks/stage4/p07-controls-v5.json"
    assert controls["previous_controls_hash_contract"] == "git_text_lf_bytes"
    expected = "f4c8fba47a90f4cbc653d732b001969b0bb7e9a01f8ccb5c109ce1217d0698d1"
    assert controls["previous_controls_sha256"] == controls["previous_controls_git_lf_sha256"] == expected
    assert lf_hash(BASE / "p07-controls-v5.json") == expected
    assert controls["corpus_git_lf_sha256"] == lf_hash(BASE / "corpus-p07-v2.json")


def test_only_3362_changes_and_other29_controls_remain_identical():
    before = load("p07-controls-v5.json")["tasks"]
    after = load("p07-controls-v6.json")["tasks"]
    assert len(after) == 30 and set(after) == set(before)
    assert {task for task in after if after[task] != before[task]} == {TASK}


def test_wrong_mutation_binds_exact_gold_test_patch_and_single_production_line():
    task = next(item for item in load("corpus-p07-v2.json")["tasks"] if item["instance_id"] == TASK)
    control = load("p07-controls-v6.json")["tasks"][TASK]
    gold = task["patch"]
    assert hashlib.sha256(gold.encode()).hexdigest() == control["gold_sha256"]
    assert control["gold_sha256"] == "23e64f2da841d5d49874221e915a084e9bb1def2fcd6d6a24fd8e7dce71fba8d"
    assert hashlib.sha256(task["test_patch"].encode()).hexdigest() == control["test_patch_sha256"]
    assert control["test_patch_sha256"] == "cbb63761e0d50033c347fb0847126632ba4c8848db8bd7f598e21d3998d5c260"
    assert gold.count(control["old"]) == 1
    assert control["new"] == "+        yield from iterator; return"
    wrong = gold.replace(control["old"], control["new"])
    assert hashlib.sha256(wrong.encode()).hexdigest() == control["wrong_sha256"]
    assert control["wrong_sha256"] == "9d0a89d0040b05a3ca5ddda9b252553b397612b99c35b8979f5c1f00aaac4946"
    assert [line for line in gold.splitlines() if not line.startswith("+")] == [
        line for line in wrong.splitlines() if not line.startswith("+")]
    assert [line for line in wrong.splitlines() if line.startswith("+++ ")] == ["+++ b/requests/utils.py"]
    assert "when response.encoding is None" in control["rationale"]
    assert "not a claim of Python2" in control["runtime_constraint"]


def test_mutation_preserves_hunk_counts_and_parses_without_executing_target_code():
    task = next(item for item in load("corpus-p07-v2.json")["tasks"] if item["instance_id"] == TASK)
    control = load("p07-controls-v6.json")["tasks"][TASK]
    wrong = task["patch"].replace(control["old"], control["new"])
    lines = wrong.splitlines()
    header = next(index for index, line in enumerate(lines) if line.startswith("@@ "))
    counts = re.fullmatch(r"@@ -[0-9]+,([0-9]+) \+[0-9]+,([0-9]+) @@.*", lines[header])
    assert counts is not None
    body = lines[header + 1:]
    assert sum(line.startswith((" ", "-")) for line in body) == int(counts[1])
    assert sum(line.startswith((" ", "+")) for line in body) == int(counts[2])
    # Only the new statement's syntax is parsed; no target function is imported,
    # compiled, evaluated, called or otherwise executed on this host.
    tree = ast.parse("def _syntax_only(iterator):\n" + control["new"][1:] + "\n")
    statements = tree.body[0].body
    assert isinstance(statements[0], ast.Expr) and isinstance(statements[0].value, ast.YieldFrom)
    assert isinstance(statements[0].value.value, ast.Name) and statements[0].value.value.id == "iterator"
    assert isinstance(statements[1], ast.Return) and statements[1].value is None


def test_predecessor_evidence_is_a_failed_calibration_not_a_claim_of_success():
    controls = load("p07-controls-v6.json")
    evidence = controls["predecessor_requirement_evidence"]
    raw = (ROOT / evidence["report"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == evidence["report_sha256"]
    report = json.loads(raw)
    assert report["calibrated"] is False and evidence["overall_calibrated"] is False
    assert evidence["required_test"] == "tests/test_requests.py::TestRequests::test_response_decode_unicode"
    assert [row["tests"][evidence["required_test"]] for row in report["rows"]] == ["FAILED", "PASSED", "PASSED"]
    assert controls["calibration_evidence_workflow"] == 40324021765
    assert controls["calibration_evidence_artifact"] == 12649834300
