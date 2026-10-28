"""Private Python3.6 full-source synthetic CheckSpec boundary on Linux Docker.

Provisioning must finish in the same job using the fixed reviewed tools. This
does not run historical tests, hidden controls, models or a TaskRunner journey.
Target source is copied as bytes and imported only inside isolated containers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path

import p07_legacy_transplant as transplant
import p07_sympy13971_image as provision
from p07_sympy13971_calibrate import RUNNER_SOURCES, runtime_admission
from p07_sympy_check_smoke import MODULE_PATH, ORDINARY_TEST, TEST_PATH, read_version

from atomic_flow.agents.execution import clean_environment, run_python
from atomic_flow.agents.verify import check_source_revision
from atomic_flow.check_execution import completed_check_receipt, run_check_spec
from atomic_flow.check_private_runtime import PrivateSyntaxRequest, private_syntax_receipt
from atomic_flow.check_runtime import RuntimeIdentity, validate_runtime_admission
from atomic_flow.check_spec import SYMPY_RUNNER_PATHS, CheckSpec, SymPyRunnerIdentity
from atomic_flow.config import ExecutionConfig

ROOT = Path(__file__).resolve().parents[1]
MARKER = "ATOMIC_CHECK_SPEC_RECEIPT:"
WALRUS_SOURCE = "if (value := 42):\n    pass\n"
VALID_SOURCE = "value = 42\n"
SKIP_TEST = "from sympy.utilities.pytest import skip\n\ndef test_native_identity():\n    assert 1 + 1 == 2\n\ndef test_native_probe():\n    skip('synthetic required ordinary obligation')\n"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def save(path, value):
    path.write_bytes(encoded(value))


def wheel_inventory(context, report):
    """Hash exact wheel package files without importing installed target code."""
    wheel = context / report["wheel"]["filename"]
    raw = wheel.read_bytes()
    if sha(raw) != report["wheel"]["sha256"]:
        raise ValueError("Retained wheel identity differs")
    result = {}
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            name = info.filename
            if info.is_dir() or not name.startswith("mpmath/"):
                continue
            if (any(part in {"", ".", ".."} for part in name.split("/"))
                    or not re.fullmatch(r"[A-Za-z0-9_./-]{1,512}", name)
                    or name in result or info.file_size > 4 * 1024**2):
                raise ValueError("Unreviewed wheel package member")
            result[name] = sha(archive.read(info))
    if not result or len(result) > 4096:
        raise ValueError("Bounded complete mpmath package inventory required")
    return result


def inventory_probe_source(expected):
    return r'''
import hashlib,json,os,sys
expected=json.loads(EXPECTED_JSON)
prefix='/opt/p07-python36/lib/python3.6/site-packages/'
if (list(sys.version_info[:3])!=[3,6,15] or sys.prefix!='/opt/p07-python36'
        or sys.executable!='/opt/p07-python36/bin/python3.6' or not sys.flags.isolated):
    raise SystemExit(125)
observed={}
for name,digest in expected.items():
    path=prefix+name
    if os.path.realpath(path)!=path or not os.path.isfile(path):raise SystemExit(125)
    with open(path,'rb') as stream:raw=stream.read(4*1024*1024+1)
    actual=hashlib.sha256(raw).hexdigest()
    if len(raw)>4*1024*1024 or actual!=digest:raise SystemExit(125)
    observed[name]=actual
actual_files=set()
for directory,dirs,files in os.walk(prefix+'mpmath'):
    dirs[:]=[name for name in dirs if name!='__pycache__']
    for name in files:
        if name.endswith(('.pyc','.pyo')):continue
        actual_files.add(os.path.relpath(os.path.join(directory,name),prefix))
if actual_files!=set(expected):raise SystemExit(125)
print(json.dumps({'files':observed,'python_version':list(sys.version_info[:3]),
                  'interpreter':sys.executable,'prefix':sys.prefix},sort_keys=True))
'''.replace("EXPECTED_JSON", repr(json.dumps(expected, sort_keys=True)))


def validate_native_case(label, spec, receipt, observed, revision, mpmath_sha):
    expected_counts = {key: 0 for key in ("passed", "failed", "skipped", "xfailed", "xpassed", "exceptions")}
    expected_counts["passed"] = 1 + int(label == "edited_pass")
    expected_counts[{"base_fail": "failed", "edited_pass": "passed", "required_skip": "skipped"}[label]] += int(label != "edited_pass")
    return (receipt.source_before == receipt.source_after == revision and receipt.spec_digest == spec.digest
            and receipt.check_passed == (label == "edited_pass") and receipt.verified_success is False
            and receipt.result.returncode == {"base_fail": 1, "edited_pass": 0, "required_skip": 5}[label]
            and not receipt.result.timed_out and not receipt.result.stdout_truncated and not receipt.result.stderr_truncated
            and observed.get("counts") == expected_counts and observed.get("collected") == 2
            and observed.get("files") == [TEST_PATH] and observed.get("finished") is True
            and observed.get("runtime_digest") == spec.runtime_identity.digest
            and observed.get("check_spec_digest") == spec.digest and observed.get("source_revision") == revision
            and observed.get("launcher_sha256") == spec.runtime_identity.launcher_sha256
            and observed.get("mpmath_module_sha256") == mpmath_sha and observed.get("mpmath_version") == "1.2.1"
            and observed.get("python_version") == [3, 6, 15]
            and observed.get("native_source_sha256") == dict(spec.sympy_identity.source_sha256)
            and (completed_check_receipt(spec, receipt, negative=label == "base_fail")
                 if label != "required_skip" else not completed_check_receipt(spec, receipt)))


def smoke(runtime_receipt, runtime_context, output):
    if platform.system() != "Linux" or output.exists():
        raise ValueError("Fresh output on an approved Linux Docker worker required")
    output.mkdir(parents=True)
    report = {"status": "not_qualified", "scope": "private Python3.6 synthetic syntax and native CheckSpec boundary",
              "provider_calls": 0, "model_calls": 0, "host_target_imports": False, "target_setup_executed": False,
              "historical_target_tests": False, "taskrunner_wired": False, "verified_success": False,
              "independent_requirement_success": False, "cases": [], "syntax": []}
    names = []
    try:
        with tempfile.TemporaryDirectory(prefix="p07-private-native-") as directory:
            private = Path(directory)
            private.chmod(0o755)
            home = private / "operator-home"
            home.mkdir(mode=0o700)
            environment = clean_environment(home)
            number = 0

            def checked(args, timeout=60):
                nonlocal number
                number += 1
                tag = f"command-{number:02d}"
                try:
                    process = subprocess.run(args, cwd=ROOT, env=environment, capture_output=True, timeout=timeout, check=False)
                except subprocess.TimeoutExpired as error:
                    (output / (tag + ".stdout")).write_bytes(error.stdout or b"")
                    (output / (tag + ".stderr")).write_bytes(error.stderr or b"")
                    raise
                (output / (tag + ".stdout")).write_bytes(process.stdout)
                (output / (tag + ".stderr")).write_bytes(process.stderr)
                save(output / (tag + ".json"), {"command": args[:3], "exit_code": process.returncode})
                if process.returncode or len(process.stdout) + len(process.stderr) > 4 * 1024**2:
                    raise RuntimeError("Fixed provisioning/admission command failed: " + args[1])
                return process.stdout

            report["tested_commit"] = checked(["git", "rev-parse", "HEAD"]).decode().strip()
            if not re.fullmatch(r"[0-9a-f]{40}", report["tested_commit"]):
                raise ValueError("Exact host commit required")
            raw_receipt = (runtime_receipt / "sympy-runtime.json").read_bytes()
            provisioned = json.loads(raw_receipt)
            actual_image = json.loads(checked(["docker", "image", "inspect", provisioned["image_id"]]))[0]
            actual_base = json.loads(checked(["docker", "image", "inspect", transplant.BASE]))[0]
            actual_donor = json.loads(checked(["docker", "image", "inspect", transplant.DONOR]))[0]
            admitted = runtime_admission(provisioned["image_id"], runtime_receipt, provision.PROFILE,
                                         actual_image, actual_base, actual_donor)
            for label, value in (("image", actual_image), ("base", actual_base), ("donor", actual_donor)):
                save(output / (label + "-inspect.json"), value)
            (output / "provisioning-receipt.json").write_bytes(raw_receipt)
            expected_files = wheel_inventory(runtime_context, provisioned)
            inventory_name = "p07-private-inventory-" + uuid.uuid4().hex[:24]
            names.append(inventory_name)
            inventory_raw = checked(["docker", "run", "--rm", "--name", inventory_name, "--pull=never", "--network=none",
                                     "--read-only", "--user=65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                                     "--memory=1g", "--memory-swap=1g", "--cpus=2", "--pids-limit=128",
                                     "--tmpfs=/tmp:rw,noexec,nosuid,size=134217728,mode=1777",
                                     "--entrypoint=" + transplant.LAUNCHER, actual_image["Id"], "-c",
                                     inventory_probe_source(expected_files)])
            inventory = json.loads(inventory_raw)
            if inventory.get("files") != expected_files:
                raise ValueError("Fresh installed mpmath byte inventory differs")
            (output / "installed-inventory.json").write_bytes(inventory_raw)
            source_name = "p07-private-source-" + uuid.uuid4().hex[:24]
            names.append(source_name)
            checked(["docker", "create", "--name", source_name, "--network=none", "--read-only", "--user=65534:65534",
                     "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=1g", "--cpus=2", "--pids-limit=128",
                     "--entrypoint=/bin/sleep", actual_image["Id"], "infinity"])
            config = json.loads(checked(["docker", "inspect", source_name]))[0]
            host = config["HostConfig"]
            if (config.get("Image") != actual_image["Id"] or config.get("Mounts") or host.get("Binds")
                    or host.get("Privileged") or not host.get("ReadonlyRootfs") or host.get("NetworkMode") != "none"
                    or host.get("CapDrop") != ["ALL"] or "no-new-privileges" not in host.get("SecurityOpt", [])
                    or host.get("Memory") != 1024**3 or host.get("NanoCpus") != 2_000_000_000
                    or host.get("PidsLimit") != 128 or config["Config"].get("User") != "65534:65534"
                    or config["State"].get("Running")):
                raise ValueError("Stopped source container policy differs")
            save(output / "source-container-inspect.json", config)
            workspace = private / "workspace"
            workspace.mkdir(mode=0o755)
            checked(["docker", "cp", source_name + ":/testbed/.", str(workspace)], 180)
            launcher = private / "launcher"
            checked(["docker", "cp", source_name + ":" + transplant.LAUNCHER, str(launcher)])
            if launcher.is_symlink() or launcher.read_bytes() != transplant.LAUNCHER_SOURCE.encode():
                raise ValueError("Actual launcher bytes differ")
            for path in workspace.rglob("*"):
                if path.is_symlink() or not (path.is_file() or path.is_dir()):
                    raise ValueError("Copied source contains links or nonregular files")
            before_strip = {path.relative_to(workspace).as_posix(): sha(path.read_bytes())
                            for path in workspace.rglob("*") if path.is_file()}
            stripped = []
            for relative, digest in sorted(before_strip.items()):
                if relative.endswith((".pyc", ".pyo")):
                    (workspace / relative).unlink()
                    stripped.append({"path": relative, "sha256": digest})
            after_strip = {path.relative_to(workspace).as_posix(): sha(path.read_bytes())
                           for path in workspace.rglob("*") if path.is_file()}
            expected_retained = {path: digest for path, digest in before_strip.items()
                                 if not path.endswith((".pyc", ".pyo"))}
            if after_strip != expected_retained:
                raise ValueError("Disposable source bytecode cleanup changed retained source bytes")
            save(output / "disposable-bytecode-removal.json", {
                "scope": "copied disposable /testbed only; installed private runtime untouched",
                "removed": stripped, "before_file_count": len(before_strip), "after_file_count": len(after_strip),
                "before_inventory_sha256": sha(encoded(before_strip)),
                "after_inventory_sha256": sha(encoded(after_strip)), "retained_bytes_unchanged": True})
            save(output / "copied-source-before-bytecode-removal.json", before_strip)
            save(output / "copied-source-after-bytecode-removal.json", after_strip)
            for path in workspace.rglob("*"):
                path.chmod(0o755 if path.is_dir() else 0o644)
            profile_raw = provision.PROFILE.read_bytes()
            helper_raw = encoded({name: sha((ROOT / name).read_bytes()) for name in (
                "tools/p07_legacy_transplant.py", "tools/p07_sympy13971_image.py",
                "src/atomic_flow/check_private_sympy.py", "src/atomic_flow/check_private_runtime.py")})
            runtime = RuntimeIdentity(base_image=transplant.BASE, base_image_id=actual_base["Id"], image_id=actual_image["Id"],
                                      interpreter=transplant.PREFIX + "/bin/python3.6", python_version=(3, 6, 15),
                                      runner="sympy-native", profile_sha256=sha(profile_raw), helper_sha256=sha(helper_raw),
                                      inventory_sha256=sha(inventory_raw), provisioning_receipt_sha256=sha(raw_receipt),
                                      private_prefix=transplant.PREFIX, launcher=transplant.LAUNCHER, launcher_sha256=sha(launcher.read_bytes()))
            validate_runtime_admission(runtime, base_inspect=actual_base, image_inspect=actual_image,
                                       profile_bytes=profile_raw, helper_bytes=helper_raw, inventory_bytes=inventory_raw,
                                       provisioning_receipt_bytes=raw_receipt, launcher_bytes=launcher.read_bytes())
            save(output / "runtime-identity.json", runtime.as_dict())
            (output / "runtime-profile.json").write_bytes(profile_raw)
            (output / "helper-inventory.json").write_bytes(helper_raw)
            hashes = tuple((path, sha((workspace / path).read_bytes())) for path in SYMPY_RUNNER_PATHS)
            if dict(hashes) != RUNNER_SOURCES:
                raise ValueError("Full source native runner anchors differ from accepted base")
            version, version_source = read_version(workspace)
            identity = SymPyRunnerIdentity((3, 6, 15), version, hashes)
            report.update(runtime_digest=runtime.digest, full_source_revision=check_source_revision(workspace),
                          full_source_file_count=sum(path.is_file() for path in workspace.rglob("*")),
                          version_source=version_source, sympy_identity=identity.as_dict())
            if any((workspace / path).exists() for path in (MODULE_PATH, TEST_PATH)):
                raise ValueError("Synthetic test/source paths must be absent")

            def make_spec(label):
                return CheckSpec(task_id="p07_private_native_" + label, task_commit=report["tested_commit"],
                                 base_commit=transplant.COMMIT, source_revision=check_source_revision(workspace),
                                 image=runtime.image_id, interpreter=runtime.interpreter, runner="sympy-native",
                                 test_paths=(TEST_PATH,), target_root="/testbed", sympy_identity=identity, runtime_identity=runtime)

            (workspace / TEST_PATH).write_bytes(ORDINARY_TEST.encode())
            for label, raw, expected in (("valid36", VALID_SOURCE, "passed"), ("walrus_rejected", WALRUS_SOURCE, "failed")):
                report["current_case"] = label
                case = output / label
                case.mkdir()
                (workspace / MODULE_PATH).write_bytes(raw.encode())
                spec = make_spec(label)
                save(case / "spec.json", spec.as_dict())
                (case / "synthetic-production.py").write_bytes(raw.encode())
                before = spec.source_revision
                request = PrivateSyntaxRequest(runtime, spec.digest, before, MODULE_PATH, sha(raw.encode()), uuid.uuid4().hex, "/testbed")
                result = run_python(list(spec.argv[1:]), workspace,
                                    config=ExecutionConfig(mode="docker", profile="checks", image=spec.image,
                                                           output_bytes=spec.resources.output_bytes_per_stream),
                                    timeout=spec.timeout_seconds, host_spec=spec, host_spec_nonce=request.nonce,
                                    host_syntax_request=request)
                observed = private_syntax_receipt(result, request)
                after = check_source_revision(workspace)
                save(case / "request.json", request.as_dict())
                save(case / "result.json", {"returncode": result.returncode, "timed_out": result.timed_out,
                                            "stdout_truncated": result.stdout_truncated, "stderr_truncated": result.stderr_truncated,
                                            "source_before": before, "source_after": after, "receipt": observed})
                (case / "stdout.txt").write_bytes(result.stdout.encode())
                (case / "stderr.txt").write_bytes(result.stderr.encode())
                if observed is None or observed["outcome"] != expected or after != before:
                    raise ValueError("Target-grammar syntax boundary mismatch")
                report["syntax"].append({"case": label, "outcome": observed["outcome"], "source_before": before, "source_after": after})
            for label, value, test in (("base_fail", 1, ORDINARY_TEST), ("edited_pass", 42, ORDINARY_TEST), ("required_skip", 42, SKIP_TEST)):
                report["current_case"] = label
                case = output / label
                case.mkdir()
                (workspace / MODULE_PATH).write_bytes(("value = " + str(value) + "\n").encode())
                (workspace / TEST_PATH).write_bytes(test.encode())
                (case / "synthetic-production.py").write_bytes((workspace / MODULE_PATH).read_bytes())
                (case / "synthetic-ordinary-test.py").write_bytes((workspace / TEST_PATH).read_bytes())
                spec = make_spec(label)
                save(case / "spec.json", spec.as_dict())
                receipt = run_check_spec(spec, workspace)
                save(case / "receipt.json", receipt.as_dict())
                (case / "stdout.txt").write_bytes(receipt.result.stdout.encode())
                (case / "stderr.txt").write_bytes(receipt.result.stderr.encode())
                observations = [json.loads(line[len(MARKER):]) for line in receipt.result.stdout.splitlines() if line.startswith(MARKER)]
                if len(observations) != 1:
                    raise ValueError("One complete private native receipt required")
                after = check_source_revision(workspace)
                qualified = (after == spec.source_revision and dict(hashes) == {path: sha((workspace / path).read_bytes()) for path in SYMPY_RUNNER_PATHS}
                             and validate_native_case(label, spec, receipt, observations[0], spec.source_revision, admitted["mpmath_module_sha256"]))
                row = {"case": label, "status": "expected_private_boundary_behavior" if qualified else "boundary_mismatch",
                       "source_before": receipt.source_before, "source_after": after, "check_passed": receipt.check_passed,
                       "process_exit": receipt.result.returncode, "verified_success": receipt.verified_success,
                       "stdout_sha256": sha(receipt.result.stdout.encode()), "stderr_sha256": sha(receipt.result.stderr.encode())}
                report["cases"].append(row)
                save(case / "result.json", row)
            report.pop("current_case", None)
            if len(report["syntax"]) == 2 and len(report["cases"]) == 3 and all(row["status"] == "expected_private_boundary_behavior" for row in report["cases"]):
                report["status"] = "private_sympy_check_boundary_qualified"
    except Exception as error:
        report.update(status="not_qualified", error_type=type(error).__name__, error=str(error)[:500])
    finally:
        cleanup = []
        environment = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}}
        for name in reversed(names):
            row = {"container": name, "confirmed": False}
            try:
                removed = subprocess.run(["docker", "rm", "-f", name], env=environment, capture_output=True, timeout=30, check=False)
                absent = subprocess.run(["docker", "container", "ls", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"],
                                        env=environment, capture_output=True, timeout=15, check=False)
                row.update(remove_exit=removed.returncode, confirmed=absent.returncode == 0 and not absent.stdout.strip())
                (output / (name + "-cleanup.stdout")).write_bytes(removed.stdout + absent.stdout)
                (output / (name + "-cleanup.stderr")).write_bytes(removed.stderr + absent.stderr)
            except (OSError, subprocess.SubprocessError) as error:
                row["error_type"] = type(error).__name__
            cleanup.append(row)
        report["cleanup"] = cleanup
        report["cleanup_confirmed"] = bool(cleanup) and all(row["confirmed"] for row in cleanup)
        if names and not report["cleanup_confirmed"]:
            report["status"] = "cleanup_uncertain"
        save(output / "summary.json", report)
        save(output / "artifact-sha256.json", {path.relative_to(output).as_posix(): sha(path.read_bytes())
                                               for path in output.rglob("*") if path.is_file() and path.name != "artifact-sha256.json"})
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-receipt", type=Path, required=True)
    parser.add_argument("--runtime-context", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = smoke(args.runtime_receipt, args.runtime_context, args.output)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "private_sympy_check_boundary_qualified" else 1)
