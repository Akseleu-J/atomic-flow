"""A prospective7114 control must preserve predecessor and production scope."""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "benchmarks/stage4"
TASK = "pylint-dev__pylint-7114"


def lf_hash(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load(name):
    return json.loads((BASE / name).read_text(encoding="utf-8"))


def test_v5_pins_immutable_predecessor_corpus_and_native_probe():
    controls = load("p07-controls-v5.json")
    assert controls["version"] == 5 and controls["status"] == "prospective_ungraded"
    assert controls["calibration_result"] == "not_run" and controls["model_visible"] is False
    assert controls["changed_tasks"] == [TASK]
    assert controls["previous_controls_file"] == "benchmarks/stage4/p07-controls-v4.json"
    assert controls["previous_controls_hash_contract"] == "git_text_lf_bytes"
    assert controls["previous_controls_git_lf_sha256"] == lf_hash(BASE / "p07-controls-v4.json")
    assert controls["previous_controls_git_lf_sha256"] == "e301135f5d6d866cfe553206b279235f9ad58a1509855993573ba0725e869038"
    assert controls["corpus_git_lf_sha256"] == lf_hash(BASE / "corpus-p07-v2.json")
    assert controls["calibration_evidence_workflow"] == 40371778294
    assert controls["calibration_evidence_runtime"] == "eb12cd7aaa7ab52b028d66b0c43cf80cccd07645"
    assert controls["calibration_evidence_archive_sha256"] == "f84534dae61922aae3abe6417fe936f276e53b4bc5ba63748948359674ade999"


def test_only_7114_changes_and_other29_controls_remain_identical():
    before = load("p07-controls-v4.json")["tasks"]
    after = load("p07-controls-v5.json")["tasks"]
    assert len(after) == 30 and set(after) == set(before)
    assert {task for task in after if after[task] != before[task]} == {TASK}
    assert all(after[task] == before[task] for task in after if task != TASK)


def test_control_changes_one_added_production_line_and_preserves_gold_identity():
    task = next(item for item in load("corpus-p07-v2.json")["tasks"] if item["instance_id"] == TASK)
    control = load("p07-controls-v5.json")["tasks"][TASK]
    gold = task["patch"]
    assert hashlib.sha256(gold.encode()).hexdigest() == control["gold_sha256"]
    assert control["gold_sha256"] == "1089cc6d5d8cda7a9b882bf08403e799a317acfa0a82638665df736ea6c3a6c4"
    assert gold.count(control["old"]) == 1 and control["old"] != control["new"]
    wrong = gold.replace(control["old"], control["new"])
    assert [line for line in gold.splitlines() if not line.startswith("+")] == [
        line for line in wrong.splitlines() if not line.startswith("+")]
    assert [line for line in wrong.splitlines() if line.startswith("+++ ")] == [
        "+++ b/pylint/lint/expand_modules.py"]
    assert "+            except ImportError as ex:" in wrong
    assert all(marker not in control["new"] for marker in ("identical", "tests/", "test_", "7114", "sys.modules"))


def test_existence_mutation_preserves_files_but_keeps_namespace_directory_defect(tmp_path):
    file = tmp_path / "ordinary.py"
    file.write_text("", encoding="utf-8")
    namespace = tmp_path / "namespace"
    namespace.mkdir()
    package = tmp_path / "package"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    missing = tmp_path / "missing"

    def branch(predicate, path):
        return predicate(path) or os.path.exists(path / "__init__.py")

    assert branch(os.path.isfile, file) and not branch(os.path.isdir, file)
    assert branch(os.path.exists, file)
    assert not branch(os.path.isfile, namespace) and branch(os.path.exists, namespace)
    assert branch(os.path.exists, package) and not branch(os.path.exists, missing)
    assert all(branch(os.path.exists, path) == os.path.exists(path) for path in (file, namespace, package, missing))
