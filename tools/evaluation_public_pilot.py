"""Run the explicitly reviewed public SymPy21614 grader pilot, no model calls.

Requires an already fetched local repository and an isolated interpreter with
pytest8.4.2/mpmath1.2.1. Does not install dependencies, fetch source or infer that
other historical environments work. The exposed pilot is not a blind holdout.
"""
import argparse
import json
import subprocess
from pathlib import Path

from evaluation_stage4 import calibrate, digest, load_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mirror", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    task = next(t for t in load_manifest(root / "benchmarks/stage4/corpus.json")["tasks"]
                if t["instance_id"] == "sympy__sympy-21614")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    interpreter = Path(args.python).resolve()
    targets = output / "oracle-targets.json"
    names = task["FAIL_TO_PASS"] + task["PASS_TO_PASS"]
    target_file = "sympy/core/tests/test_kind.py"
    targets.write_text(json.dumps({"targets": [target_file], "aliases": {n: target_file + "::" + n for n in names}}, indent=2))
    command = [str(interpreter), "-I", str(root / "tools/evaluation_pytest.py"), "--workspace", "{workspace}",
               "--report", "{report}", "--tests", str(targets)]
    wrong = task["patch"].replace("+        return self.args[0].kind", "+        return S.Zero.kind")
    if wrong == task["patch"]:
        raise ValueError("reviewed wrong-patch anchor changed")
    (output / "wrong.patch").write_bytes(wrong.encode())
    (output / "host-config.json").write_text(json.dumps({"evaluator": command, "wrong_patch": str(output / "wrong.patch"), "timeout": 180}, indent=2))
    if args.prepare_only:
        print(str(output / "host-config.json"))
        return 0
    report = calibrate(task, args.mirror, command, output, wrong, timeout=180)
    identity = subprocess.check_output([str(interpreter), "-I", "-c",
                                        "import sys,pytest,mpmath; print(sys.version); print('pytest',pytest.__version__); print('mpmath',mpmath.__version__)"],
                                       text=True, timeout=30)
    report.update(environment_identity=identity, interpreter_sha256=digest(interpreter.read_bytes()),
                  oracle_adapter_sha256=digest((root / "tools/evaluation_pytest.py").read_bytes()),
                  task_exposure="public_known_gold_development_pilot", model_calls=0, provider_calls=0)
    report["installed_dependencies"] = json.loads(subprocess.check_output([str(interpreter), "-I", "-m", "pip", "list", "--format=json"], text=True, timeout=30))
    (output / "calibration.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"instance_id": task["instance_id"], "calibrated": report["calibrated"], "model_quality_measured": False}))
    return 0 if report["calibrated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
