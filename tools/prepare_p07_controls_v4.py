"""Materialize prospective Sphinx controls without rewriting earlier evidence."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "benchmarks/stage4"
CHANGES = {
    "sphinx-doc__sphinx-8506": {
        "old": r"+option_desc_re = re.compile(r'((?:/|--|-|\+)?[^\s=]+)(=?\s*.*)')",
        "new": r"+option_desc_re = re.compile(r'((?:/|--|-|\+)?[^\s=\[][^\s=]*)(=?\s*.*)')",
        "gold_sha256": "a99517671ca1aec8033a000c5212ab6f1a80afe4563e0e8c76889577e67e4a7b",
        "rationale": "Incomplete bracket support permits brackets after the first option character, preserving optional-value handling, but rejects a leading bracket. The production grammar category is generic; no test-specific predicate is introduced.",
    },
    "sphinx-doc__sphinx-8713": {
        "old": "+            return self._format_docutils_params(fields)",
        "new": "+            return self._format_fields(_('Other Parameters'), fields)",
        "gold_sha256": "790ea5dc9611d778d1147d4eeb2945d413961932546340f910c9c0b41846ee8d",
        "rationale": "Consume grouped Other Parameters correctly but return section-field formatting even when napoleon_use_param requires docutils parameter fields. Ordinary Parameters and the false configuration branch remain unchanged.",
    },
    "sphinx-doc__sphinx-8721": {
        "old": '+    if app.builder.name.startswith("epub") and not env.config.viewcode_enable_epub:',
        "new": '+    if app.builder.name.startswith("epub3") and not env.config.viewcode_enable_epub:',
        "gold_sha256": "b280454dbde68b9fd743d03c4d07172306c61477330f337287c48af6e8fced04",
        "rationale": "Narrow the output-family guard incorrectly to epub3, leaving ordinary epub ungated. HTML output, the singlehtml guard and explicitly enabled epub behavior retain the gold path.",
    },
}


def lf_hash(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def materialize(output, *, predecessor=BASE / "p07-controls-v3.json", corpus=BASE / "corpus-p07-v2.json"):
    if output.exists():
        raise ValueError("fresh prospective controls output required")
    previous = json.loads(predecessor.read_text(encoding="utf-8"))
    tasks = {task["instance_id"]: task for task in json.loads(corpus.read_text(encoding="utf-8"))["tasks"]}
    if previous["version"] != 3 or len(tasks) != 30 or set(previous["tasks"]) != set(tasks):
        raise ValueError("exact v3 predecessor and complete unchanged corpus required")
    result = copy.deepcopy(previous)
    for task_id, control in CHANGES.items():
        gold = tasks[task_id]["patch"]
        if (hashlib.sha256(gold.encode()).hexdigest() != control["gold_sha256"]
                or previous["tasks"][task_id]["gold_sha256"] != control["gold_sha256"]
                or gold.count(control["old"]) != 1 or control["old"] == control["new"]):
            raise ValueError("control does not match the exact reviewed gold source")
        result["tasks"][task_id] = dict(control)
    result.pop("calibration_evidence_workflow", None)
    result.update(version=4, status="prospective_ungraded", model_visible=False,
                  previous_controls_file="benchmarks/stage4/p07-controls-v3.json",
                  previous_controls_git_lf_sha256=lf_hash(predecessor),
                  corpus_git_lf_sha256=lf_hash(corpus), changed_tasks=sorted(CHANGES),
                  calibration_evidence_directory="validation/p07-budget20/sphinx-profile-v1/",
                  calibration_evidence_runtime_prefix="f3649ca", calibration_result="not_run")
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=BASE / "p07-controls-v4.json")
    result = materialize(parser.parse_args().output)
    print(json.dumps({"version": result["version"], "status": result["status"], "changed_tasks": result["changed_tasks"]}))
