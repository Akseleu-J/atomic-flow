"""Exercise the real pytest-self CheckSpec boundary on an approved Linux worker.

Only synthetic ordinary tests run against the complete byte-copied source.
This is executor evidence, not a TaskRunner journey or historical issue success.
No hidden tests or gold patch are loaded. Target imports happen only in Docker.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path

from atomic_flow.agents.execution import clean_environment
from atomic_flow.agents.verify import check_source_revision
from atomic_flow.check_execution import completed_check_receipt, run_check_spec
from atomic_flow.check_pytest_self import PytestSelfIdentity, pytest_self_args
from atomic_flow.check_spec import CheckSpec

ROOT = Path(__file__).resolve().parents[1]
CALIBRATION = ROOT / "validation/p07-budget20/calibration-v2/pytest-dev__pytest-6116.json"
IMAGE = "swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-6116@sha256:5fb21e0b90b8df4b1da5876db062a0be7f601d12bce36ac6ae5c8a72b0005be9"
BASE_COMMIT = "e670ff76cbad80108bde9bab616b66771b8653cf"
INTERPRETER = "/opt/miniconda3/envs/testbed/bin/python"
TEST_PATH = "testing/test_p07_self_check.py"
MODULE_PATH = "src/_pytest/p07_probe.py"
ANCHORS = ("src/_pytest/__init__.py", "src/pytest.py")
MARKER = "ATOMIC_CHECK_SPEC_RECEIPT:"
ORDINARY_TEST = "from _pytest.p07_probe import value\n\ndef test_identity():\n    assert 1 + 1 == 2\n\ndef test_probe():\n    assert value == 42\n"
SKIP_TEST = "import pytest\n\ndef test_identity():\n    assert 1 + 1 == 2\n\ndef test_probe():\n    pytest.skip('synthetic required ordinary obligation')\n"
PROBE = r'''
import json,os,sys,tempfile
if not sys.flags.isolated or os.path.realpath(os.getcwd())!='/testbed':
    raise SystemExit(125)
sys.dont_write_bytecode=True
sys.pycache_prefix=tempfile.mkdtemp(prefix='p07-pytest-probe-')
sys.path.insert(0,'/testbed/src')
import pytest,_pytest
value={'python_version':list(sys.version_info[:3]),'pytest_version':pytest.__version__,
       'interpreter':sys.executable,'pytest_module':os.path.realpath(pytest.__file__),
       '_pytest_module':os.path.realpath(_pytest.__file__)}
if (value['interpreter']!='/opt/miniconda3/envs/testbed/bin/python'
        or value['pytest_module']!='/testbed/src/pytest.py'
        or value['_pytest_module']!='/testbed/src/_pytest/__init__.py'):
    raise SystemExit(125)
print('P07_PYTEST_SELF_METADATA:'+json.dumps(value,sort_keys=True),flush=True)
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.write_bytes((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def validate_case(label, receipt, spec, identity, revision, observed):
    """Validate the complete actual executor result, including required skips."""
    expected_exit = {"base_fail": 1, "edited_pass": 0, "required_skip": 5}[label]
    result = receipt.result
    expected_counts = {"collected": 2, "completed": 2, "collection_errors": 0,
                       "skipped": int(label == "required_skip"), "failed": int(label == "base_fail"),
                       "call_failed": int(label == "base_fail"), "setup_failed": 0, "teardown_failed": 0}
    return (receipt.source_before == receipt.source_after == revision
            and receipt.spec_digest == spec.digest and receipt.check_passed == (label == "edited_pass")
            and receipt.verified_success is False and result.returncode == expected_exit
            and not result.timed_out and not result.stdout_truncated and not result.stderr_truncated
            and observed.get("runner") == "pytest-self" and observed.get("finished") is True
            and observed.get("collection_complete") is True and observed.get("files") == [TEST_PATH]
            and observed.get("python_version") == list(identity.python_version)
            and observed.get("pytest_version") == identity.pytest_version
            and observed.get("pytest_module") == "/testbed/src/pytest.py"
            and observed.get("_pytest_module") == "/testbed/src/_pytest/__init__.py"
            and observed.get("pytest_self_identity") == identity.as_dict()
            and observed.get("exit_code") == (1 if label == "base_fail" else 0)
            and all(type(observed.get(key)) is int and observed[key] == count for key, count in expected_counts.items())
            and (completed_check_receipt(spec, receipt, negative=label == "base_fail")
                 if label != "required_skip" else not completed_check_receipt(spec, receipt)))


def smoke(output):
    if platform.system() != "Linux" or output.exists():
        raise ValueError("Fresh output on an approved Linux Docker worker required")
    calibration_raw = CALIBRATION.read_bytes()
    calibration = json.loads(calibration_raw)
    if (calibration.get("instance_id") != "pytest-dev__pytest-6116" or calibration.get("calibrated") is not True
            or calibration.get("image_digest") != IMAGE or calibration.get("base_commit") != BASE_COMMIT):
        raise ValueError("Exact accepted pytest source-image calibration required")
    output.mkdir(parents=True)
    (output / "calibration-input.json").write_bytes(calibration_raw)
    report = {"status": "not_qualified", "scope": "pytest-self CheckSpec synthetic ordinary-test boundary only",
              "provider_calls": 0, "model_calls": 0, "provider_credentials": "not_forwarded",
              "target_setup_executed": False, "host_target_imports": False, "historical_target_tests": False,
              "taskrunner_wired": False, "independent_requirement_success": False, "verified_success": False,
              "image": IMAGE, "base_commit": BASE_COMMIT, "calibration_sha256": sha(calibration_raw),
              "smoke_tool_sha256": sha(Path(__file__).read_bytes()), "cases": []}
    source_name = "p07-pytest-source-" + uuid.uuid4().hex[:24]
    probe_name = "p07-pytest-probe-" + uuid.uuid4().hex[:24]
    attempted = []
    try:
        with tempfile.TemporaryDirectory(prefix="p07-pytest-source-") as directory:
            private = Path(directory)
            private.chmod(0o755)
            home = private / "operator-home"
            home.mkdir(mode=0o700)
            environment = clean_environment(home)
            counter = 0

            def execute(args, timeout=60):
                nonlocal counter
                counter += 1
                tag = f"provision-{counter:02d}"
                try:
                    process = subprocess.run(args, cwd=ROOT, env=environment, capture_output=True,
                                             timeout=timeout, check=False)
                except subprocess.TimeoutExpired as error:
                    (output / (tag + ".stdout")).write_bytes(error.stdout or b"")
                    (output / (tag + ".stderr")).write_bytes(error.stderr or b"")
                    save(output / (tag + ".json"), {"command": args[:3], "timed_out": True})
                    raise
                (output / (tag + ".stdout")).write_bytes(process.stdout)
                (output / (tag + ".stderr")).write_bytes(process.stderr)
                save(output / (tag + ".json"), {"command": args[:3], "exit_code": process.returncode})
                return process

            def checked(args, timeout=60):
                process = execute(args, timeout)
                if process.returncode or len(process.stdout) + len(process.stderr) > 4 * 1024**2:
                    raise RuntimeError("Fixed provisioning command failed or exceeded output bound: " + args[1])
                return process.stdout.decode().strip()

            report["tested_commit"] = checked(["git", "rev-parse", "HEAD"])
            if not re.fullmatch(r"[0-9a-f]{40}", report["tested_commit"]):
                raise ValueError("Exact host source commit required")
            checked(["docker", "pull", IMAGE], 600)
            image = json.loads(checked(["docker", "image", "inspect", IMAGE]))[0]
            if (IMAGE not in image.get("RepoDigests", []) or image.get("Id") != calibration.get("image_id")
                    or image.get("Os") != "linux"
                    or image.get("Architecture") != "amd64"
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("Id", ""))):
                raise ValueError("Actual source image identity differs")
            report["image_id"] = image["Id"]
            save(output / "image-inspect.json", image)
            attempted.append(source_name)
            checked(["docker", "create", "--name", source_name, "--network", "none", "--read-only",
                     "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                     "--memory", "1g", "--cpus", "2", "--pids-limit", "128", "--entrypoint", "/bin/sleep",
                     image["Id"], "infinity"])
            config = json.loads(checked(["docker", "inspect", source_name]))[0]
            policy = config["HostConfig"]
            if (config.get("Image") != image["Id"] or config.get("Mounts") or policy.get("Binds")
                    or policy.get("Privileged") or not policy.get("ReadonlyRootfs")
                    or policy.get("NetworkMode") != "none" or policy.get("CapDrop") != ["ALL"]
                    or "no-new-privileges" not in policy.get("SecurityOpt", [])
                    or policy.get("Memory") != 1024**3 or policy.get("NanoCpus") != 2_000_000_000
                    or policy.get("PidsLimit") != 128 or config["Config"].get("User") != "65534:65534"
                    or config["State"].get("Running")):
                raise ValueError("Stopped source provisioning policy differs")
            save(output / "source-container-inspect.json", config)
            workspace = private / "workspace"
            workspace.mkdir(mode=0o755)
            checked(["docker", "cp", source_name + ":/testbed/.", str(workspace)], 180)
            for path in workspace.rglob("*"):
                if path.is_symlink() or not (path.is_dir() or path.is_file()):
                    raise ValueError("Copied source contains a link or nonregular entry")
            for path in workspace.rglob("*"):
                path.chmod(0o755 if path.is_dir() else 0o644)
            source_before_probe = check_source_revision(workspace)
            anchors = tuple((relative, sha((workspace / relative).read_bytes())) for relative in ANCHORS)
            report.update(full_source_revision=source_before_probe,
                          full_source_file_count=sum(path.is_file() for path in workspace.rglob("*")),
                          protected_source_sha256=dict(anchors))
            attempted.append(probe_name)
            probe = execute(["docker", "run", "--rm", "--name", probe_name, "--pull=never", "--network=none",
                             "--read-only", "--user=65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                             "--memory=1g", "--memory-swap=1g", "--cpus=2", "--pids-limit=128",
                             "--tmpfs=/tmp:rw,nosuid,nodev,size=134217728,mode=1777", "--env=HOME=/tmp",
                             "--workdir=/testbed", "--mount=type=bind,src=" + str(workspace) + ",dst=/testbed,readonly",
                             "--entrypoint=" + INTERPRETER, image["Id"], "-I", "-B", "-c", PROBE])
            (output / "runtime-probe.stdout").write_bytes(probe.stdout)
            (output / "runtime-probe.stderr").write_bytes(probe.stderr)
            lines = [line.removeprefix("P07_PYTEST_SELF_METADATA:") for line in probe.stdout.decode().splitlines()
                     if line.startswith("P07_PYTEST_SELF_METADATA:")]
            if probe.returncode or len(lines) != 1 or len(probe.stdout) + len(probe.stderr) > 4 * 1024**2:
                raise ValueError("Isolated source runtime metadata probe failed")
            metadata = json.loads(lines[0])
            if (metadata.get("interpreter") != INTERPRETER or metadata.get("pytest_module") != "/testbed/src/pytest.py"
                    or metadata.get("_pytest_module") != "/testbed/src/_pytest/__init__.py"
                    or source_before_probe != check_source_revision(workspace)):
                raise ValueError("Source/runtime identity changed during metadata probe")
            identity = PytestSelfIdentity(python_version=tuple(metadata["python_version"]),
                                          pytest_version=metadata["pytest_version"], source_sha256=anchors)
            save(output / "runtime-identity.json", identity.as_dict())
            report["pytest_self_identity"] = identity.as_dict()
            if any((workspace / path).exists() for path in (MODULE_PATH, TEST_PATH)):
                raise ValueError("Synthetic probe paths must be absent in the pinned source")
            for label, value, test in (("base_fail", 1, ORDINARY_TEST), ("edited_pass", 42, ORDINARY_TEST),
                                       ("required_skip", 42, SKIP_TEST)):
                case = output / label
                case.mkdir()
                (workspace / MODULE_PATH).write_bytes(("value = " + str(value) + "\n").encode())
                (workspace / TEST_PATH).write_bytes(test.encode())
                (case / "synthetic-production.py").write_bytes((workspace / MODULE_PATH).read_bytes())
                (case / "synthetic-ordinary-test.py").write_bytes((workspace / TEST_PATH).read_bytes())
                revision = check_source_revision(workspace)
                report["current_case"] = label
                spec = CheckSpec(task_id="p07_pytest_self_synthetic_" + label, task_commit=report["tested_commit"],
                                 base_commit=BASE_COMMIT, source_revision=revision, image=IMAGE,
                                 interpreter=INTERPRETER, runner="pytest-self", test_paths=(TEST_PATH,),
                                 target_root="/testbed", pytest_self_identity=identity)
                save(case / "spec.json", spec.as_dict())
                save(case / "source-before.json", {"revision": revision, "protected_source_sha256": dict(anchors)})
                receipt = run_check_spec(spec, workspace)
                save(case / "receipt.json", receipt.as_dict())
                (case / "stdout.txt").write_bytes(receipt.result.stdout.encode())
                (case / "stderr.txt").write_bytes(receipt.result.stderr.encode())
                observations = [json.loads(line[len(MARKER):]) for line in receipt.result.stdout.splitlines()
                                if line.startswith(MARKER)]
                if len(observations) != 1:
                    raise ValueError("Exactly one counted pytest-self observation required")
                observed = observations[0]
                after = check_source_revision(workspace)
                current_anchors = {relative: sha((workspace / relative).read_bytes()) for relative in ANCHORS}
                save(case / "source-after.json", {"revision": after, "protected_source_sha256": current_anchors})
                qualified = (validate_case(label, receipt, spec, identity, revision, observed)
                             and after == revision and current_anchors == dict(anchors))
                row = {"case": label, "status": "expected_pytest_self_boundary_behavior" if qualified else "boundary_mismatch",
                       "check_passed": receipt.check_passed, "process_exit": receipt.result.returncode,
                       "verified_success": receipt.verified_success, "source_before": receipt.source_before,
                       "source_after": receipt.source_after, "spec_digest": spec.digest, "runner_receipt": observed,
                       "stdout_sha256": sha(receipt.result.stdout.encode()), "stderr_sha256": sha(receipt.result.stderr.encode()),
                       "cleanup": "confirmed_by_existing_executor_return"}
                report["cases"].append(row)
                save(case / "result.json", row)
            report.pop("current_case", None)
            if len(report["cases"]) == 3 and all(row["status"] == "expected_pytest_self_boundary_behavior" for row in report["cases"]):
                report["status"] = "pytest_self_check_boundary_qualified"
    except Exception as error:
        report.update(status="not_qualified", error_type=type(error).__name__, error=str(error)[:500])
    finally:
        cleanup = []
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL"}}
        for name in reversed(attempted):
            row = {"name": name, "confirmed": False}
            try:
                removed = subprocess.run(["docker", "rm", "-f", name], env=environment,
                                         capture_output=True, timeout=30, check=False)
                absent = subprocess.run(["docker", "container", "ls", "-a", "--filter", "name=^/" + name + "$",
                                         "--format", "{{.ID}}"], env=environment, capture_output=True, timeout=15, check=False)
                row.update(remove_exit=removed.returncode, confirmed=absent.returncode == 0 and not absent.stdout.strip())
                (output / (name + "-cleanup.stdout")).write_bytes(removed.stdout + absent.stdout)
                (output / (name + "-cleanup.stderr")).write_bytes(removed.stderr + absent.stderr)
            except (OSError, subprocess.SubprocessError) as error:
                row["error_type"] = type(error).__name__
            cleanup.append(row)
        report["provisioning_cleanup"] = cleanup
        report["source_container_cleanup_confirmed"] = bool(cleanup) and all(row["confirmed"] for row in cleanup)
        if attempted and not report["source_container_cleanup_confirmed"]:
            report["status"] = "cleanup_uncertain"
        save(output / "summary.json", report)
    return report


def prepare(workspace: Path, identity: PytestSelfIdentity, image: str,
            interpreter: str, test_paths: tuple[str, ...], nonce: str) -> dict:
    if re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}", image) is None:
        raise ValueError("exact dependency image digest required")
    workspace = workspace.absolute()
    if not workspace.is_dir() or "," in str(workspace):
        raise ValueError("real workspace without Docker mount delimiter required")
    if any(path.is_symlink() or getattr(path, "is_junction", lambda: False)()
           for path in (workspace, *workspace.parents)):
        raise ValueError("workspace links are not admitted")
    args = pytest_self_args(identity, nonce, "/workspace", interpreter, test_paths)
    argv = ["docker", "run", "--rm", "--pull=never", "--network=none", "--read-only",
            "--user=65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--memory=1g", "--memory-swap=1g", "--cpus=2", "--pids-limit=128",
            "--tmpfs=/tmp:rw,nosuid,nodev,size=134217728,mode=1777", "--env=HOME=/tmp",
            "--workdir=/workspace",
            "--mount=type=bind,src=" + str(workspace) + ",dst=/workspace,readonly",
            "--entrypoint=" + interpreter, image, *args]
    return {"kind": "prepared_pytest_self_bootstrap_probe", "executed": False,
            "integrated_check_spec": False, "runtime_registry_admission_required": True,
            "ordinary_test_inventory_admission_required": True,
            "nonce": nonce, "image": image, "identity": identity.as_dict(),
            "argv": argv, "shell_command_linux": shlex.join(argv),
            "remaining_gates": ["runtime registry/image/interpreter/dependency admission",
                                "bounded lifecycle execution and source-before/after verification",
                                "receipt validation", "TaskRunner integration", "real Docker evidence"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = smoke(args.output)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "pytest_self_check_boundary_qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
