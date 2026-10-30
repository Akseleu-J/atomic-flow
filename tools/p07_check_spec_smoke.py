"""Real Linux Docker CheckSpec smoke using only host-generated synthetic tests.

This qualifies the standalone execution boundary, not a historical target task,
TaskRunner wiring, model quality or independent oracle success. Image pulling is
trusted provisioning; each actual check keeps the existing network-none sandbox.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import tempfile
from pathlib import Path

from atomic_flow.agents.execution import clean_environment
from atomic_flow.agents.verify import check_source_revision
from atomic_flow.check_execution import run_check_spec
from atomic_flow.check_spec import CheckSpec

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "benchmarks/stage4/p07-pylint-environment-profile.json"
IMAGE = ("swebench/sweb.eval.x86_64.pylint-dev_1776_pylint-7080@sha256:"
         "3389f0d1319ca611d04a209fc30990c131e0bae9acfeb2163938c75ad23479fe")
INTERPRETER = "/opt/miniconda3/envs/testbed/bin/python"
CASES = {
    "pass": "from sample import answer\n\ndef test_answer():\n    assert answer() == 42\n",
    "assertion_fail": "from sample import answer\n\ndef test_answer():\n    assert answer() == 0\n",
    "required_skip": "import pytest\n\ndef test_answer():\n    pytest.skip('synthetic required obligation')\n",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.write_bytes((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def smoke(output):
    if platform.system() != "Linux":
        raise ValueError("CheckSpec smoke requires an approved Linux Docker worker")
    if output.exists():
        raise ValueError("Fresh smoke output required; no implicit retries")
    profile_bytes = PROFILE.read_bytes().replace(b"\r\n", b"\n")
    records = [row for row in json.loads(profile_bytes)["tasks"]
               if row["instance_id"] == "pylint-dev__pylint-7080"]
    if len(records) != 1 or records[0]["base_image_digest"] != IMAGE:
        raise ValueError("Reviewed immutable smoke image differs from the task profile")
    output.mkdir(parents=True)
    report = {"status": "not_qualified", "scope": "standalone CheckSpec synthetic boundary only",
              "model_calls": 0, "provider_credentials": "not_forwarded", "historical_target_tests": False,
              "taskrunner_wired": False, "verified_success": False, "image": IMAGE,
              "profile_git_lf_sha256": sha(profile_bytes), "cases": []}
    source_paths = [Path(__file__).resolve(), ROOT / "src/atomic_flow/check_spec.py",
                    ROOT / "src/atomic_flow/check_execution.py", ROOT / "src/atomic_flow/agents/execution.py"]
    report["host_source_sha256"] = {path.relative_to(ROOT).as_posix(): sha(path.read_bytes().replace(b"\r\n", b"\n"))
                                    for path in source_paths}
    try:
        with tempfile.TemporaryDirectory(prefix="p07-check-spec-operator-") as private_home:
            environment = clean_environment(Path(private_home))

            def command(args, *, timeout=60, log=None):
                if log is not None:
                    with (output / log).open("wb") as stream:
                        result = subprocess.run(args, cwd=ROOT, env=environment, stdout=stream,
                                                stderr=subprocess.STDOUT, timeout=timeout, check=False)
                    if result.returncode:
                        raise RuntimeError("Trusted provisioning failed; inspect retained log")
                    return ""
                result = subprocess.run(args, cwd=ROOT, env=environment, capture_output=True,
                                        timeout=timeout, check=False)
                if result.returncode:
                    raise RuntimeError("Trusted image/source inspection failed")
                if len(result.stdout) + len(result.stderr) > 4 * 1024**2:
                    raise ValueError("Trusted inspection output exceeds receipt bound")
                return result.stdout.decode().strip()

            commit = command(["git", "rev-parse", "HEAD"])
            if not re.fullmatch(r"[0-9a-f]{40}", commit):
                raise ValueError("Exact tested source commit unavailable")
            report["tested_commit"] = commit
            command(["docker", "pull", IMAGE], timeout=600, log="image-pull.log")
            image = json.loads(command(["docker", "image", "inspect", IMAGE]))[0]
            if (IMAGE not in image.get("RepoDigests", []) or image.get("Os") != "linux"
                    or image.get("Architecture") != "amd64"
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("Id", ""))):
                raise ValueError("Resolved smoke image identity differs")
            report["image_id"] = image["Id"]
            save(output / "image-inspect.json", image)

            for label, tests in CASES.items():
                row = {"case": label, "expected_check_passed": label == "pass", "status": "not_completed"}
                case_output = output / label
                case_output.mkdir()
                try:
                    with tempfile.TemporaryDirectory(prefix="p07-check-spec-" + label + "-") as directory:
                        workspace = Path(directory)
                        # Root-run CI still uses the executor's non-root UID. The
                        # synthetic directory has no secrets and needs traversal.
                        workspace.chmod(0o755)
                        files = {"sample.py": b"def answer():\n    return 42\n",
                                 "test_example.py": tests.encode()}
                        for name, raw in files.items():
                            path = workspace / name
                            path.write_bytes(raw)
                            path.chmod(0o644)
                        revision = check_source_revision(workspace)
                        spec = CheckSpec(task_id="p07_synthetic_" + label, task_commit=commit,
                                         base_commit=records[0]["base_commit"], source_revision=revision,
                                         image=IMAGE, interpreter=INTERPRETER, runner="pytest",
                                         test_paths=("test_example.py",), target_root="/testbed")
                        save(case_output / "spec.json", spec.as_dict())
                        row.update(source_revision=revision, spec_digest=spec.digest,
                                   synthetic_source_sha256={name: sha(raw) for name, raw in files.items()})
                        receipt = run_check_spec(spec, workspace)
                        save(case_output / "check-receipt.json", receipt.as_dict())
                        (case_output / "stdout.txt").write_bytes(receipt.result.stdout.encode())
                        (case_output / "stderr.txt").write_bytes(receipt.result.stderr.encode())
                        unchanged = all((workspace / name).read_bytes() == raw for name, raw in files.items())
                        marker = "ATOMIC_CHECK_SPEC_RECEIPT:"
                        observations = [json.loads(line[len(marker):]) for line in receipt.result.stdout.splitlines()
                                        if line.startswith(marker)]
                        if len(observations) != 1:
                            raise ValueError("Synthetic runner did not supply exactly one completed observation")
                        observed = observations[0]
                        expected_failed = 1 if label == "assertion_fail" else 0
                        expected_skipped = 1 if label == "required_skip" else 0
                        expected_process_exit = {"pass": 0, "assertion_fail": 1, "required_skip": 5}[label]
                        observed_expected = (observed.get("root") == spec.target_root
                                             and observed.get("interpreter") == INTERPRETER
                                             and observed.get("finished") is True and observed.get("collected") == 1
                                             and observed.get("files") == ["test_example.py"]
                                             and observed.get("failed") == expected_failed
                                             and observed.get("skipped") == expected_skipped
                                             and observed.get("exit_code") == expected_failed)
                        save(case_output / "synthetic-runner-observation.json", observed)
                        row.update(check_passed=receipt.check_passed, verified_success=receipt.verified_success,
                                   process_exit=receipt.result.returncode, source_bytes_unchanged=unchanged,
                                   source_after=receipt.source_after,
                                   cleanup="confirmed_by_existing_executor_return",
                                   stdout_sha256=sha(receipt.result.stdout.encode()),
                                   stderr_sha256=sha(receipt.result.stderr.encode()))
                        accepted = (unchanged and receipt.source_before == receipt.source_after == revision
                                    and receipt.check_passed == row["expected_check_passed"]
                                    and receipt.verified_success is False
                                    and not receipt.result.timed_out and not receipt.result.stdout_truncated
                                    and not receipt.result.stderr_truncated
                                    and observed_expected and receipt.result.returncode == expected_process_exit)
                        row["status"] = "expected_boundary_behavior" if accepted else "boundary_mismatch"
                except Exception as error:
                    row.update(status="execution_not_confirmed", error_type=type(error).__name__,
                               error=str(error)[:500], cleanup="not_claimed_after_executor_exception")
                finally:
                    report["cases"].append(row)
                    save(case_output / "result.json", row)
            if len(report["cases"]) == 3 and all(row["status"] == "expected_boundary_behavior" for row in report["cases"]):
                report["status"] = "synthetic_check_spec_boundary_qualified"
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error)[:500])
    finally:
        save(output / "summary.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    result = smoke(**vars(parser.parse_args()))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "synthetic_check_spec_boundary_qualified" else 1)
