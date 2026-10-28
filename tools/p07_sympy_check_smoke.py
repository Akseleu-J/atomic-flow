"""Real native SymPy CheckSpec boundary on an isolated Docker worker.

Only synthetic ordinary tests are run. This is not a TaskRunner journey, model
evaluation, independent hidden oracle, or historical task-success measurement.
Target source is copied as bytes and never imported or executed on the host.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from atomic_flow.agents.execution import clean_environment
from atomic_flow.agents.verify import check_source_revision
from atomic_flow.check_execution import run_check_spec
from atomic_flow.check_spec import SYMPY_RUNNER_PATHS, CheckSpec, SymPyRunnerIdentity

ROOT = Path(__file__).resolve().parents[1]
CALIBRATION = ROOT / "validation/p07-budget20/calibration-v2/sympy__sympy-20212.json"
IMAGE = "swebench/sweb.eval.x86_64.sympy_1776_sympy-20212@sha256:10acbbc49c1905919809c0d8ca6305ee0d5e546fa541246c18fca3e222f79626"
INTERPRETER = "/opt/miniconda3/envs/testbed/bin/python"
TEST_PATH = "sympy/core/tests/test_p07_native_check.py"
MODULE_PATH = "sympy/p07_probe.py"
ORDINARY_TEST = "from sympy.p07_probe import value\n\ndef test_native_identity():\n    assert 1 + 1 == 2\n\ndef test_native_probe():\n    assert value == 42\n"
SKIP_TEST = "from sympy.testing.pytest import skip\n\ndef test_native_identity():\n    assert 1 + 1 == 2\n\ndef test_native_probe():\n    skip('synthetic required ordinary obligation')\n"
MARKER = "ATOMIC_CHECK_SPEC_RECEIPT:"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.write_bytes((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def read_version(workspace):
    """Read only literal source text, including the historical release module."""
    source = (workspace / "sympy/__init__.py").read_text(encoding="utf-8")
    literal = re.search(r"^__version__\s*=\s*(['\"])([A-Za-z0-9_.+-]+)\1\s*$", source, re.MULTILINE)
    if literal:
        return literal[2], "sympy/__init__.py"
    if re.search(r"^from (?:\.release|sympy\.release) import __version__\s*$", source, re.MULTILINE):
        source = (workspace / "sympy/release.py").read_text(encoding="utf-8")
        literal = re.search(r"^__version__\s*=\s*(['\"])([A-Za-z0-9_.+-]+)\1\s*$", source, re.MULTILINE)
        if literal:
            return literal[2], "sympy/release.py"
    raise ValueError("No admitted literal native SymPy version found; never import target on host")


def smoke(output, *, host_callback=None):
    """Provision fixed byte source; optional callback is trusted host tooling only.

    The CLI never accepts a callback, target code, or model-supplied hook.
    Default triple execution and its output schema remain unchanged.
    """
    if host_callback is not None and not callable(host_callback):
        raise TypeError("trusted host callback must be callable")
    if platform.system() != "Linux" or output.exists():
        raise ValueError("Fresh output on an approved Linux Docker worker required")
    calibration_raw = CALIBRATION.read_bytes()
    calibration = json.loads(calibration_raw)
    if (calibration.get("instance_id") != "sympy__sympy-20212"
            or calibration.get("calibrated") is not True or calibration.get("image_digest") != IMAGE):
        raise ValueError("Exact accepted source-image identity required")
    output.mkdir(parents=True)
    report = {"status": "not_qualified", "scope": "native CheckSpec synthetic ordinary-test boundary only",
              "provider_calls": 0, "model_calls": 0, "provider_credentials": "not_forwarded",
              "target_setup_executed": False, "host_target_imports": False,
              "historical_target_tests": False, "taskrunner_wired": False,
              "independent_requirement_success": False, "verified_success": False,
              "image": IMAGE, "calibration_sha256": sha(calibration_raw), "cases": []}
    container = "p07-native-source-" + uuid.uuid4().hex[:24]
    create_attempted = False
    try:
        with tempfile.TemporaryDirectory(prefix="p07-native-source-") as directory:
            private = Path(directory)
            private.chmod(0o755)
            home = private / "operator-home"
            home.mkdir(mode=0o700)
            environment = clean_environment(home)

            def execute(args, timeout=60):
                return subprocess.run(args, cwd=ROOT, env=environment, capture_output=True, timeout=timeout, check=False)

            def checked(args, timeout=60):
                process = execute(args, timeout)
                if process.returncode or len(process.stdout) + len(process.stderr) > 4 * 1024**2:
                    raise RuntimeError("Trusted fixed provisioning command failed or exceeded output bound: " + args[1])
                return process.stdout.decode().strip()

            report["tested_commit"] = checked(["git", "rev-parse", "HEAD"])
            if not re.fullmatch(r"[0-9a-f]{40}", report["tested_commit"]):
                raise ValueError("Exact host source commit required")
            pulled = execute(["docker", "pull", IMAGE], 600)
            (output / "image-pull.stdout").write_bytes(pulled.stdout)
            (output / "image-pull.stderr").write_bytes(pulled.stderr)
            if pulled.returncode:
                raise RuntimeError("Immutable accepted image pull failed")
            image = json.loads(checked(["docker", "image", "inspect", IMAGE]))[0]
            if (IMAGE not in image.get("RepoDigests", []) or image.get("Os") != "linux"
                    or image.get("Architecture") != "amd64"
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("Id", ""))):
                raise ValueError("Actual source image identity differs")
            report["image_id"] = image["Id"]
            save(output / "image-inspect.json", image)
            create_attempted = True
            checked(["docker", "create", "--name", container, "--network", "none", "--read-only",
                     "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                     "--memory", "1g", "--cpus", "2", "--pids-limit", "128", "--entrypoint", "/bin/sleep",
                     image["Id"], "infinity"])
            config = json.loads(checked(["docker", "inspect", container]))[0]
            policy = config["HostConfig"]
            if (config.get("Image") != image["Id"] or config.get("Mounts") or policy.get("Binds")
                    or policy.get("Privileged") or not policy.get("ReadonlyRootfs")
                    or policy.get("NetworkMode") != "none" or policy.get("CapDrop") != ["ALL"]
                    or "no-new-privileges" not in policy.get("SecurityOpt", [])
                    or policy.get("Memory") != 1024**3 or policy.get("NanoCpus") != 2_000_000_000
                    or policy.get("PidsLimit") != 128 or config["Config"].get("User") != "65534:65534"
                    or config["State"].get("Running")):
                raise ValueError("Source-only provisioning container policy differs")
            save(output / "source-container-inspect.json", config)
            workspace = private / "workspace"
            workspace.mkdir(mode=0o755)
            # A stopped, inspected container is only a byte source; no target
            # setup, import, test, or arbitrary Docker exec runs on the host.
            checked(["docker", "cp", container + ":/testbed/.", str(workspace)], 180)
            for path in workspace.rglob("*"):
                if path.is_symlink():
                    raise ValueError("Copied source contains a link; no traversal or execution admitted")
                if not path.is_dir() and not path.is_file():
                    raise ValueError("Copied source contains a nonregular entry")
            for path in workspace.rglob("*"):
                path.chmod(0o755 if path.is_dir() else 0o644)
            runner_hashes = tuple((relative, sha((workspace / relative).read_bytes())) for relative in SYMPY_RUNNER_PATHS)
            version, version_source = read_version(workspace)
            identity = SymPyRunnerIdentity(python_version=(3, 9, 20), sympy_version=version, source_sha256=runner_hashes)
            report.update(native_identity=identity.as_dict(), version_source=version_source,
                          version_source_sha256=sha((workspace / version_source).read_bytes()))
            if (workspace / MODULE_PATH).exists() or (workspace / TEST_PATH).exists():
                raise ValueError("Synthetic source paths must be absent in the pinned image")
            if host_callback is not None:
                host_callback(workspace, identity, calibration, report, output)
            else:
                for label, value, test in (("base_fail", 1, ORDINARY_TEST), ("edited_pass", 42, ORDINARY_TEST),
                                           ("required_skip", 42, SKIP_TEST)):
                    case = output / label
                    case.mkdir()
                    (workspace / MODULE_PATH).write_bytes(("value = " + str(value) + "\n").encode())
                    (workspace / TEST_PATH).write_bytes(test.encode())
                    revision = check_source_revision(workspace)
                    spec = CheckSpec(task_id="p07_native_synthetic_" + label, task_commit=report["tested_commit"],
                                     base_commit=calibration["base_commit"], source_revision=revision,
                                     image=IMAGE, interpreter=INTERPRETER, runner="sympy-native", test_paths=(TEST_PATH,),
                                     target_root="/testbed", sympy_identity=identity)
                    save(case / "spec.json", spec.as_dict())
                    receipt = run_check_spec(spec, workspace)
                    save(case / "receipt.json", receipt.as_dict())
                    (case / "stdout.txt").write_bytes(receipt.result.stdout.encode())
                    (case / "stderr.txt").write_bytes(receipt.result.stderr.encode())
                    observations = [json.loads(line[len(MARKER):]) for line in receipt.result.stdout.splitlines() if line.startswith(MARKER)]
                    if len(observations) != 1:
                        raise ValueError("Exactly one completed counted native observation required")
                    observed = observations[0]
                    expected_counts = {key: int(key == {"base_fail": "failed", "edited_pass": "passed", "required_skip": "skipped"}[label])
                                       for key in ("passed", "failed", "skipped", "xfailed", "xpassed", "exceptions")}
                    expected_counts['passed'] += 1
                    expected_exit = {"base_fail": 1, "edited_pass": 0, "required_skip": 5}[label]
                    row = {"case": label, "check_passed": receipt.check_passed, "process_exit": receipt.result.returncode,
                           "verified_success": receipt.verified_success, "source_before": receipt.source_before,
                           "source_after": receipt.source_after, "spec_digest": spec.digest,
                           "stdout_sha256": sha(receipt.result.stdout.encode()), "stderr_sha256": sha(receipt.result.stderr.encode()),
                           "runner_receipt": observed, "cleanup": "confirmed_by_existing_executor_return"}
                    qualified = (receipt.source_before == receipt.source_after == revision == check_source_revision(workspace)
                                 and receipt.check_passed == (label == "edited_pass") and receipt.verified_success is False
                                 and receipt.result.returncode == expected_exit and not receipt.result.timed_out
                                 and not receipt.result.stdout_truncated and not receipt.result.stderr_truncated
                                 and observed.get("runner") == "sympy-native" and observed.get("finished") is True
                                 and observed.get("python_version") == [3, 9, 20] and observed.get("sympy_version") == version
                                 and observed.get("sympy_module") == "/testbed/sympy/__init__.py"
                                 and observed.get("files") == [TEST_PATH] and observed.get("collected") == 2
                                 and observed.get("counts") == expected_counts
                                 and observed.get("native_source_sha256") == dict(runner_hashes)
                                 and receipt.result.stdout.count("ATOMIC_SYMPY_BEGIN:") == 1
                                 and receipt.result.stdout.count("ATOMIC_SYMPY_END:") == 1)
                    row["status"] = "expected_native_boundary_behavior" if qualified else "boundary_mismatch"
                    report["cases"].append(row)
                    save(case / "result.json", row)
                if len(report["cases"]) == 3 and all(row["status"] == "expected_native_boundary_behavior" for row in report["cases"]):
                    report["status"] = "native_sympy_check_boundary_qualified"
    except Exception as error:
        report.update(status="not_qualified", error_type=type(error).__name__, error=str(error)[:500])
    finally:
        if create_attempted:
            try:
                clean = {key: value for key, value in __import__("os").environ.items() if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL"}}
                removed = subprocess.run(["docker", "rm", "-f", container], env=clean, capture_output=True, timeout=30, check=False)
                absent = subprocess.run(["docker", "container", "ls", "-a", "--filter", "name=^/" + container + "$", "--format", "{{.ID}}"],
                                        env=clean, capture_output=True, timeout=15, check=False)
                report.update(source_container_cleanup_exit=removed.returncode,
                              source_container_cleanup_confirmed=absent.returncode == 0 and not absent.stdout.strip())
            except (OSError, subprocess.SubprocessError) as error:
                report.update(source_container_cleanup_confirmed=False, cleanup_error_type=type(error).__name__)
            if not report["source_container_cleanup_confirmed"]:
                report["status"] = "cleanup_uncertain"
        save(output / "summary.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    result = smoke(parser.parse_args().output)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "native_sympy_check_boundary_qualified" else 1)
