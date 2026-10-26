"""P07 calibration on official SWE-bench images, without host project execution.

The external harness is imported only by the Linux worker entrypoint. Model
credentials are never provided to this worker or its network-disabled containers.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import subprocess
import time
import uuid
from pathlib import Path

from evaluation_stage4 import load_manifest, patch_paths, protected_test_path

HARNESS_VERSION = "3.0.15"
TASK_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def classify(task, observed, *, completed, exit_code):
    """Missing/error/skipped requirements cannot be accepted as assertion FAIL."""
    required = task["FAIL_TO_PASS"] + task["PASS_TO_PASS"]
    if (not completed or exit_code not in {0, 1} or not observed or "ERROR" in observed.values()
            or any(observed.get(test) not in {"PASSED", "FAILED"} for test in required)):
        return {"status": "infrastructure_error", "verified_success": False}
    regressions = (all(observed[test] == "PASSED" for test in task["PASS_TO_PASS"])
                   and not any(value == "FAILED" and test not in task["FAIL_TO_PASS"]
                               for test, value in observed.items()))
    all_pass = all(observed[test] == "PASSED" for test in required)
    unexpected_failure = any(value in {"FAILED", "ERROR"} for value in observed.values())
    success = exit_code == 0 and all_pass and not unexpected_failure
    return {"status": "verified_success" if success else "requirement_failed",
            "verified_success": success, "regressions_passed": regressions,
            "assertion_failures": [t for t in task["FAIL_TO_PASS"] if observed[t] == "FAILED"]}


def normalize_observations(task, observed):
    """Keep v3 SymPy failure-header aliases as diagnostics, not extra test cases."""
    diagnostics = {}
    if task["repo"] == "sympy/sympy":
        for key, value in observed.items():
            match = re.fullmatch(r".*\.py:([^:]+)", key)
            if match and (match[1].isdigit() or observed.get(match[1]) == value):
                diagnostics[key] = value
    return {key: value for key, value in observed.items() if key not in diagnostics}, diagnostics


def calibration_decision(rows):
    base, gold, wrong = rows
    return (base["status"] == "requirement_failed" and bool(base.get("assertion_failures"))
            and base.get("regressions_passed") is True and gold["verified_success"]
            and wrong["status"] == "requirement_failed" and bool(wrong.get("assertion_failures"))
            and wrong.get("regressions_passed") is True)


def evaluation_script(spec, install):
    """Use the pinned repo's own test command; image already contains dependencies."""
    commands = [line for line in spec.eval_script_list if line != install]
    end = next(i for i, line in enumerate(commands) if ">>>>> End Test Output" in line)
    commands.insert(end, "p07_test_exit=$?; printf '\\nP07_TEST_EXIT=%s\\n' \"$p07_test_exit\"")
    return "\n".join(["#!/bin/bash", "exec 2>&1", "set -uxo pipefail", "export PYTHONPATH=/testbed:/testbed/src",
                       "export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", *commands]) + "\n"


def runner_identity_command(repo):
    code = "import sys; print(sys.version)"
    if repo == "pytest-dev/pytest":
        code += "; import pytest,_pytest; print(pytest.__file__); print(_pytest.__file__)"
    return "source /opt/miniconda3/bin/activate && conda activate testbed && cd /testbed && " \
           "PYTHONPATH=/testbed:/testbed/src python -c '" + code + "'"


def command(args, *, timeout=60, log=None):
    if log:
        with Path(log).open("wb") as stream:
            result = subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        return result.returncode
    result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError("worker command failed: " + args[0] + ": " + result.stderr.decode(errors="replace")[:300])
    return result.stdout.decode(errors="replace").strip()


def grade(task, spec, image, patch, output, label, parser, timeout):
    name = "p07-" + task["instance_id"].lower() + "-" + uuid.uuid4().hex[:10]
    row = {"label": label, "patch_sha256": sha(patch.encode()), "status": "infrastructure_error",
           "verified_success": False, "container_name": name}
    started = time.monotonic()
    log = output / (label + ".log")
    try:
        if patch and (patch_paths(patch) & patch_paths(task["test_patch"])
                      or any(protected_test_path(p) for p in patch_paths(patch))):
            raise ValueError("patch modifies protected tests")
        command(["docker", "create", "--name", name, "--network", "none", "--cap-drop", "ALL",
                 "--security-opt", "no-new-privileges", "--memory", "6g", "--cpus", "2",
                 "--pids-limit", "256", "--entrypoint", "/bin/bash", image, "-c", "sleep infinity"])
        command(["docker", "start", name])
        config = json.loads(command(["docker", "inspect", name]))[0]
        host = config["HostConfig"]
        if (host["NetworkMode"] != "none" or host["Privileged"] or host.get("Binds")
                or host["CapDrop"] != ["ALL"] or "no-new-privileges" not in host["SecurityOpt"]
                or host["Memory"] != 6 * 1024**3 or host["PidsLimit"] != 256):
            raise ValueError("container isolation contract mismatch")
        row["isolation"] = {"network": "none", "caps": "ALL dropped", "host_mounts": [],
                            "memory_bytes": host["Memory"], "pids": host["PidsLimit"],
                            "no_new_privileges": True, "image": image}
        row["image_checkout_head"] = command(["docker", "exec", name, "git", "-C", "/testbed", "rev-parse", "HEAD"])
        # Official images can have a synthetic environment/setup commit. Prepare
        # the requested immutable revision before checking or applying any patch.
        command(["docker", "exec", name, "git", "-C", "/testbed", "reset", "--hard", task["base_commit"]])
        command(["docker", "exec", name, "git", "-C", "/testbed", "clean", "-fd"])
        base = command(["docker", "exec", name, "git", "-C", "/testbed", "rev-parse", "HEAD"])
        if base != task["base_commit"]:
            raise ValueError("image source does not match pinned task base")
        row["base_verified"] = base
        identity = runner_identity_command(task["repo"])
        row["runner_identity"] = command(["docker", "exec", name, "/bin/bash", "-c", identity])
        if task["repo"] == "pytest-dev/pytest" and sum(
                line.startswith("/testbed/") for line in row["runner_identity"].splitlines()) != 2:
            raise ValueError("historical pytest runner is not imported from pinned workspace")
        command(["docker", "cp", str(output / "eval.sh"), name + ":/tmp/p07-eval.sh"])
        if patch:
            path = output / (label + ".patch")
            path.write_bytes(patch.encode())
            command(["docker", "cp", str(path), name + ":/tmp/p07-candidate.patch"])
            command(["docker", "exec", "-w", "/testbed", name, "git", "apply", "--check", "/tmp/p07-candidate.patch"])
            command(["docker", "exec", "-w", "/testbed", name, "git", "apply", "/tmp/p07-candidate.patch"])
        before = command(["docker", "exec", "-w", "/testbed", name, "git", "diff", "--binary"])
        row["process_exit"] = command(["docker", "exec", name, "/bin/bash", "/tmp/p07-eval.sh"], timeout=timeout, log=log)
        after = command(["docker", "exec", "-w", "/testbed", name, "git", "diff", "--binary"])
        if before != after:
            raise ValueError("test execution modified tracked source or protected tests")
        text = log.read_text(errors="replace")
        # Pinned v3 harness constants supply the delimiters.
        from swebench.harness.constants import END_TEST_OUTPUT, START_TEST_OUTPUT

        start, end = START_TEST_OUTPUT, END_TEST_OUTPUT
        if start not in text or end not in text:
            raise ValueError("test output delimiters absent")
        body = text.split(start, 1)[1].split(end, 1)[0]
        exits = re.findall(r"^P07_TEST_EXIT=(\d+)$", body, re.MULTILINE)
        if len(exits) != 1:
            raise ValueError("missing or duplicate test exit receipt")
        observed, diagnostics = normalize_observations(task, parser(body, spec))
        row["tests"] = observed
        row["parser_diagnostics"] = diagnostics
        row.update(classify(task, observed, completed=True, exit_code=int(exits[0])))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        row.update(error_type=type(exc).__name__, error=str(exc)[:500])
    finally:
        cleanup = subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30, check=False)
        row["cleanup_exit"] = cleanup.returncode
        if cleanup.returncode and "No such container" not in cleanup.stderr.decode(errors="replace"):
            row.update(status="infrastructure_error", verified_success=False, cleanup_uncertain=True)
        row["wall_seconds"] = time.monotonic() - started
        if log.exists():
            row["log_sha256"] = sha(log.read_bytes())
        (output / (label + ".json")).write_text(json.dumps(row, indent=2) + "\n")
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--manifest", default="benchmarks/stage4/corpus.json")
    parser.add_argument("--controls", default="benchmarks/stage4/p07-controls.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    if not TASK_ID.fullmatch(args.task) or not 10 <= args.timeout <= 600:
        raise ValueError("invalid task or execution timeout")
    if args.output.exists():
        raise ValueError("fresh output required; no implicit retries or report replacement")
    args.output.mkdir(parents=True)
    report = {"instance_id": args.task, "status": "infrastructure_error", "calibrated": False,
              "model_calls": 0, "provider_credentials": "none", "harness": HARNESS_VERSION}
    try:
        if importlib.metadata.version("swebench") != HARNESS_VERSION:
            raise ValueError("unreviewed external evaluator version")
        from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS
        from swebench.harness.log_parsers import MAP_REPO_TO_PARSER
        from swebench.harness.test_spec.test_spec import make_test_spec

        task = next(t for t in load_manifest(args.manifest)["tasks"] if t["instance_id"] == args.task)
        control = json.loads(Path(args.controls).read_text())["tasks"][args.task]
        wrong = task["patch"].replace(control["old"], control["new"])
        if wrong == task["patch"] or not control["rationale"] or task["patch"].count(control["old"]) != 1:
            raise ValueError("reviewed negative control does not match exact task gold")
        spec = make_test_spec(task, namespace="swebench")
        image = spec.instance_image_key
        command(["docker", "pull", image], timeout=600, log=args.output / "pull.log")
        info = json.loads(command(["docker", "image", "inspect", image]))[0]
        digest = info["RepoDigests"][0]
        report.update(image_tag=image, image_digest=digest, image_id=info["Id"], base_commit=task["base_commit"],
                      manifest_sha256=sha(Path(args.manifest).read_bytes()),
                      controls_sha256=sha(Path(args.controls).read_bytes()), negative_control_rationale=control["rationale"])
        report["test_command"] = MAP_REPO_VERSION_TO_SPECS[task["repo"]][task["version"]]["test_cmd"]
        script = evaluation_script(spec, MAP_REPO_VERSION_TO_SPECS[task["repo"]][task["version"]].get("install"))
        (args.output / "eval.sh").write_text(script)
        report["eval_script_sha256"] = sha(script.encode())
        rows = [grade(task, spec, digest, patch, args.output, label, MAP_REPO_TO_PARSER[task["repo"]], args.timeout)
                for label, patch in (("base", ""), ("gold", task["patch"]), ("wrong", wrong))]
        report.update(rows=rows, calibrated=calibration_decision(rows), status="completed")
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    (args.output / "calibration.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("instance_id", "status", "calibrated", "model_calls")}))
    return 0 if report["calibrated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
