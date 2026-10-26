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
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def classify(task, observed, *, completed, exit_code, requirement_exceptions=None):
    """Keep unproven errors infrastructural; never relabel exceptions as assertions."""
    requirement_exceptions = requirement_exceptions or {}
    exception_tests = set(requirement_exceptions)
    if (exception_tests and exit_code != 1 or not exception_tests <= set(task["FAIL_TO_PASS"])
            or any(not _valid_requirement_exception(task, test, observed, receipt)
                   for test, receipt in requirement_exceptions.items())):
        return {"status": "infrastructure_error", "verified_success": False}
    required = task["FAIL_TO_PASS"] + task["PASS_TO_PASS"]
    if (not completed or exit_code not in {0, 1} or not observed
            or any(value == "ERROR" and test not in exception_tests for test, value in observed.items())
            or any(observed.get(test) not in {"PASSED", "FAILED"}
                   and not (test in exception_tests and observed.get(test) == "ERROR") for test in required)):
        return {"status": "infrastructure_error", "verified_success": False}
    regressions = (all(observed[test] == "PASSED" for test in task["PASS_TO_PASS"])
                   and not any(value == "FAILED" and test not in task["FAIL_TO_PASS"]
                               for test, value in observed.items()))
    all_pass = all(observed[test] == "PASSED" for test in required)
    unexpected_failure = any(value in {"FAILED", "ERROR"} for value in observed.values())
    success = exit_code == 0 and all_pass and not unexpected_failure
    return {"status": "verified_success" if success else "requirement_exception_failed" if exception_tests else "requirement_failed",
            "verified_success": success, "regressions_passed": regressions,
            "assertion_failures": [t for t in task["FAIL_TO_PASS"] if observed[t] == "FAILED"],
            "requirement_exception_failures": sorted(exception_tests),
            "requirement_exception_evidence": requirement_exceptions}


def _valid_requirement_exception(task, test, observed, receipt):
    """Only a completed native SymPy test body can supply this narrow receipt."""
    if not isinstance(receipt, dict) or task.get("repo") != "sympy/sympy" or observed.get(test) != "ERROR":
        return False
    frame = receipt.get("test_frame", {})
    sources = receipt.get("production_frames", [])
    if not isinstance(frame, dict):
        return False
    try:
        test_paths = patch_paths(task.get("test_patch", ""))
        production_paths = patch_paths(task.get("patch", ""))
    except (TypeError, ValueError):
        return False
    return (receipt.get("kind") == "requirement_test_body_exception" and receipt.get("test_id") == test
            and receipt.get("observed_status") == "ERROR" and receipt.get("exception_type") in {"TypeError", "ValueError"}
            and bool(task.get("base_commit")) and receipt.get("base_commit") == task["base_commit"]
            and receipt.get("native_runner_completed") is True and receipt.get("test_exit") == 1
            and frame.get("function") == test and frame.get("path") in test_paths
            and type(frame.get("line")) is int and frame["line"] > 0
            and isinstance(sources, list) and bool(sources)
            and all(isinstance(item, dict) and item.get("path") in production_paths
                    and type(item.get("line")) is int and item["line"] > 0 for item in sources)
            and all(isinstance(receipt.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", receipt[key])
                    for key in ("raw_body_sha256", "traceback_sha256")))


def test_body_exception_receipts(task, body, observed):
    """Corroborate ERROR using native runner header, exact test and changed-source frames.

    A fixture/setup/import/collection error has no such first test-body frame and
    is not admitted. Raw ERROR remains in the test map and can never be success.
    """
    if task.get("repo") != "sympy/sympy":
        return {}
    clean = ANSI.sub("", body)
    if (len(re.findall(r"^\s*(?:=+\s*)?tests finished:[^\n]*\bin [0-9.]+ seconds[^\n]*$", clean, re.MULTILINE)) != 1
            or re.findall(r"^P07_TEST_EXIT=(\d+)$", clean, re.MULTILINE) != ["1"]):
        return {}
    try:
        production_paths = patch_paths(task.get("patch", ""))
    except (TypeError, ValueError):
        return {}
    headers = list(re.finditer(r"^_+\s+(sympy/[A-Za-z0-9_./]+\.py):(test\w+)\s+_+\s*$", clean, re.MULTILINE))
    receipts = {}
    for index, header in enumerate(headers):
        path, test = header.groups()
        if (test not in task["FAIL_TO_PASS"] or observed.get(test) != "ERROR"
                or sum(candidate[2] == test for candidate in headers) != 1):
            continue
        block = clean[header.end():headers[index + 1].start() if index + 1 < len(headers) else len(clean)]
        if block.count("Traceback (most recent call last):") != 1:
            continue
        frames = [{"path": path, "line": int(line), "function": function}
                  for path, line, function in re.findall(
                      r'^  File "/testbed/([A-Za-z0-9_./]+)", line ([1-9][0-9]*), in ([A-Za-z_]\w*)$', block, re.MULTILINE)]
        exceptions = re.findall(r"^(TypeError|ValueError):[^\n]*$", block, re.MULTILINE)
        if not frames or frames[0]["path"] != path or frames[0]["function"] != test or len(exceptions) != 1:
            continue
        production = [frame for frame in frames[1:] if frame["path"] in production_paths]
        receipt = {"kind": "requirement_test_body_exception", "test_id": test, "observed_status": "ERROR",
                   "exception_type": exceptions[0], "base_commit": task.get("base_commit"), "test_frame": frames[0],
                   "native_runner_completed": True, "test_exit": 1,
                   "production_frames": production, "raw_body_sha256": sha(body.encode()),
                   "traceback_sha256": sha(block.encode())}
        if _valid_requirement_exception(task, test, observed, receipt):
            receipts[test] = receipt
    return receipts


def normalize_observations(task, observed):
    """Keep v3 SymPy failure-header aliases as diagnostics, not extra test cases."""
    diagnostics = {}
    if task["repo"] == "sympy/sympy":
        for key, value in observed.items():
            match = re.fullmatch(r".*\.py:([^:]+)", key)
            if match and (match[1].isdigit() or observed.get(match[1]) == value
                          or value == "FAILED" and observed.get(match[1]) == "ERROR"):
                diagnostics[key] = value
    return {key: value for key, value in observed.items() if key not in diagnostics}, diagnostics


def parse_observations(task, body, parser, spec):
    """Use the outer pytest summary, not intentionally failing nested self-tests.

    Keep original logs and discarded observations for audit. Missing required
    outer tests and collection errors still fail admission in classify().
    """
    clean = ANSI.sub("", body)
    normalized = clean
    if task["repo"] != "sympy/sympy":
        # Official old-version parsers miss verbose statuses followed by pytest
        # progress percentages. Change presentation only, keeping the node ID.
        normalized = re.sub(r"^(\S+::.*?)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)(?:\s+\[\s*\d+%\])?\s*$",
                            lambda match: match[2] + " " + match[1], clean, flags=re.MULTILINE)
    else:
        # Native SymPy appends the file's aggregate [OK]/[FAIL] marker to its
        # final testcase line. It is not that testcase's own result. Preserve
        # the testcase token and remove presentation suffix before v3 parsing.
        normalized = re.sub(r"^(test\w+)\s+(ok|F|E|f|s|w)\s+\[(?:OK|FAIL)\]\s*$",
                            lambda match: match[1] + " " + match[2], clean, flags=re.MULTILINE)
    source, scope = normalized, "complete_test_output"
    if task["repo"] == "pytest-dev/pytest":
        summaries = list(re.finditer(r"^=+ short test summary info =+\s*$", normalized, re.MULTILINE))
        if summaries:
            tail = normalized[summaries[-1].end():]
            # A completed outer terminal summary is necessary; a captured inner
            # summary without an outer summary cannot turn an aborted run green.
            footer = re.search(r"^=+ [^\n]*(?:passed|failed|error|skipped|xfailed|xpassed)[^\n]* in [^\n]*=+\s*$",
                               tail, re.MULTILINE)
            if footer:
                source = tail[:footer.start()]
                scope = "final_outer_pytest_short_summary"
    observed, aliases = normalize_observations(task, parser(source, spec))
    raw = parser(normalized, spec)
    diagnostics = {"scope": scope, "ansi_removed": clean != body,
                   "verbose_status_normalized": task["repo"] != "sympy/sympy" and normalized != clean,
                   "native_status_normalized": task["repo"] == "sympy/sympy" and normalized != clean,
                   "failure_header_aliases": aliases,
                   "header_status_mismatches": {key: {"header": value, "test": observed.get(key.rsplit(":", 1)[-1])}
                        for key, value in aliases.items()
                        if observed.get(key.rsplit(":", 1)[-1]) is not None
                        and observed.get(key.rsplit(":", 1)[-1]) != value},
                   "discarded_nested_or_alias_observations": {key: value for key, value in raw.items()
                        if key not in observed or observed[key] != value}}
    return observed, diagnostics


def calibration_decision(rows):
    base, gold, wrong = rows

    def negative(row):
        exceptions = row.get("requirement_exception_failures", [])
        return (row["status"] in {"requirement_failed", "requirement_exception_failed"}
                and bool(row.get("assertion_failures") or exceptions)
                and (not exceptions or set(exceptions) == set(row.get("requirement_exception_evidence", {}))))

    return (negative(base)
            and base.get("regressions_passed") is True and gold["verified_success"]
            and negative(wrong)
            and wrong.get("regressions_passed") is True)


def evaluation_script(spec, install, repo=None):
    """Use the pinned repo's own test command; image already contains dependencies."""
    commands = [line for line in spec.eval_script_list if line != install]
    if repo == "sphinx-doc/sphinx":
        # These pinned tox commands otherwise print dots, making named PASS
        # receipts unavailable. Only pytest reporting flags are added after --;
        # selectors, test bodies, fixtures and assertions remain untouched.
        commands = [line + " -rA -vv --color=no" if line.startswith("tox ") and " -- " in line else line
                    for line in commands]
    end = next(i for i, line in enumerate(commands) if ">>>>> End Test Output" in line)
    commands.insert(end, "p07_test_exit=$?; printf '\\nP07_TEST_EXIT=%s\\n' \"$p07_test_exit\"")
    # Pytest self-tests must use their source checkout. For other repositories
    # rely on the official image's installed editable checkout: inherited forced
    # PYTHONPATH changes subprocess import semantics tested by pylint itself.
    pythonpath = "unset PYTHONPATH" if repo == "pylint-dev/pylint" else "export PYTHONPATH=/testbed:/testbed/src"
    return "\n".join(["#!/bin/bash", "exec 2>&1", "set -uxo pipefail", pythonpath,
                       "export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", *commands]) + "\n"


def runner_identity_command(repo):
    code = "import sys; print(sys.version)"
    if repo == "pytest-dev/pytest":
        code += "; import pytest,_pytest; print(pytest.__file__); print(_pytest.__file__)"
    elif repo in {"psf/requests", "pylint-dev/pylint", "sphinx-doc/sphinx", "sympy/sympy"}:
        module = {"psf/requests": "requests", "pylint-dev/pylint": "pylint", "sphinx-doc/sphinx": "sphinx",
                  "sympy/sympy": "sympy"}[repo]
        code += "; import " + module + "; print(" + module + ".__file__)"
    pythonpath = "" if repo == "pylint-dev/pylint" else "PYTHONPATH=/testbed:/testbed/src "
    return "source /opt/miniconda3/bin/activate && conda activate testbed && cd /testbed && " \
           + pythonpath + "python -c '" + code + "'"


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
        row["environment_packages"] = command(["docker", "exec", name, "/bin/bash", "-c",
            "source /opt/miniconda3/bin/activate && conda activate testbed && python -m pip freeze"])
        identity = runner_identity_command(task["repo"])
        row["runner_identity"] = command(["docker", "exec", name, "/bin/bash", "-c", identity])
        if task["repo"] == "pytest-dev/pytest" and sum(
                line.startswith("/testbed/") for line in row["runner_identity"].splitlines()) != 2:
            raise ValueError("historical pytest runner is not imported from pinned workspace")
        if task["repo"] != "pytest-dev/pytest" and not any(
                line.startswith("/testbed/") for line in row["runner_identity"].splitlines()):
            raise ValueError("historical target package is not imported from pinned workspace")
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
        observed, diagnostics = parse_observations(task, body, parser, spec)
        row["tests"] = observed
        row["parser_diagnostics"] = diagnostics
        exceptions = test_body_exception_receipts(task, body, observed)
        row["test_body_exception_receipts"] = exceptions
        row.update(classify(task, observed, completed=True, exit_code=int(exits[0]), requirement_exceptions=exceptions))
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
    parser.add_argument("--candidate", type=Path, help="Independent grading after fresh base/gold/wrong calibration")
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
        if control.get("gold_sha256") != sha(task["patch"].encode()):
            raise ValueError("negative control was not reviewed for this exact gold source patch")
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
        script = evaluation_script(spec, MAP_REPO_VERSION_TO_SPECS[task["repo"]][task["version"]].get("install"), task["repo"])
        (args.output / "eval.sh").write_text(script)
        report["eval_script_sha256"] = sha(script.encode())
        rows = [grade(task, spec, digest, patch, args.output, label, MAP_REPO_TO_PARSER[task["repo"]], args.timeout)
                for label, patch in (("base", ""), ("gold", task["patch"]), ("wrong", wrong))]
        report.update(rows=rows, calibrated=calibration_decision(rows), status="completed")
        if args.candidate:
            candidate_bytes = args.candidate.read_bytes()
            if not candidate_bytes or len(candidate_bytes) > 1_000_000:
                raise ValueError("candidate size outside admission bounds")
            candidate = candidate_bytes.decode("utf-8")
            paths = patch_paths(candidate)
            if not paths or any(protected_test_path(path) for path in paths):
                raise ValueError("candidate has empty or protected scope")
            report["candidate_input_sha256"] = sha(candidate_bytes)
            if report["calibrated"]:
                report["candidate"] = grade(task, spec, digest, candidate, args.output, "candidate",
                                            MAP_REPO_TO_PARSER[task["repo"]], args.timeout)
            else:
                report["candidate"] = {"status": "not_admitted_calibration_failed", "verified_success": False}
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    (args.output / "calibration.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("instance_id", "status", "calibrated", "model_calls")}))
    accepted = report["calibrated"] and (not args.candidate or report.get("candidate", {}).get("verified_success"))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
