"""Exact SymPy13971 native oracle on a separately qualified private runtime.

No target code executes on the host. This adapter admits only base/gold/wrong,
not a general command, candidate, model transport or full TaskRunner executor.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import uuid
from pathlib import Path

import evaluation_p07_isolated as oracle
import p07_legacy_transplant as transplant
from evaluation_stage4 import load_manifest, patch_paths, protected_test_path

ROOT = Path(__file__).resolve().parents[1]
TASK = "sympy__sympy-13971"
PROFILE = ROOT / "benchmarks/stage4/p07-sympy13971-runtime-profile.json"
NATIVE_ARGV = ("/testbed/bin/test", "-C", "--verbose", "sympy/printing/tests/test_latex.py")
RUNNER_SOURCES = {
    "bin/test": "973e3cf784388b8e7821e4962da8eeb4b615447cd652af920452b322bd63588c",
    "bin/get_sympy.py": "3cae7463d72dd553101977101015a862c4ce2b93f17c7ae9712f0fd2ca287869",
    "sympy/utilities/runtests.py": "f3ebc3f4677d84d64b7e52ea5129522b2b5233fd2937fe5857fbe8244dfcf95c",
}
START = ">>>>> Start Test Output"
END = ">>>>> End Test Output"
IDENTITY_PREFIX = "P07_RUNNER_IDENTITY="
LOG_LIMIT = 16 * 1024**2


def sha(raw):
    return oracle.sha(raw)


def save(path, value):
    path.write_bytes((json.dumps(value, sort_keys=True, indent=2) + "\n").encode())


def bootstrap_source(expected_mpmath_sha256):
    if not re.fullmatch(r"[0-9a-f]{64}", expected_mpmath_sha256):
        raise ValueError("exact mpmath module digest required")
    return '''import hashlib,json,os,pathlib,runpy,subprocess,sys,warnings
expected=json.loads(EXPECTED)
if os.getcwd()!='/testbed' or list(sys.version_info[:3])!=[3,6,15] or not sys.flags.isolated:
    raise RuntimeError('private runtime identity mismatch')
if sys.prefix!='/opt/p07-python36' or sys.executable!='/opt/p07-python36/bin/python3.6':
    raise RuntimeError('private interpreter prefix mismatch')
for relative,digest in expected.items():
    if hashlib.sha256((pathlib.Path('/testbed')/relative).read_bytes()).hexdigest()!=digest:
        raise RuntimeError('native runner source mismatch')
warnings.filterwarnings('ignore',category=UserWarning)
warnings.filterwarnings('ignore',category=SyntaxWarning)
sys.path[:0]=['/testbed/bin','/testbed']
import mpmath,sympy
mpath=pathlib.Path(mpmath.__file__)
if mpmath.__version__!='1.2.1' or not str(mpath).startswith('/opt/p07-python36/lib/python3.6/site-packages/mpmath/'):
    raise RuntimeError('mpmath dependency identity mismatch')
if hashlib.sha256(mpath.read_bytes()).hexdigest()!=MPMATH_SHA:
    raise RuntimeError('mpmath module differs from verified wheel')
if str(pathlib.Path(sympy.__file__).resolve())!='/testbed/sympy/__init__.py':
    raise RuntimeError('SymPy import does not resolve to target source')
child=subprocess.Popen([sys.executable,'-RV'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
try:
    child_output=child.communicate(timeout=15)[0]
except subprocess.TimeoutExpired:
    child.kill()
    child_output=child.communicate()[0]
    raise RuntimeError('native subprocess capability probe timed out')
identity={'version_info':list(sys.version_info[:3]),'executable':sys.executable,'prefix':sys.prefix,
    'isolated':sys.flags.isolated,'sympy_module':sympy.__file__,'mpmath_module':mpmath.__file__,
    'mpmath_version':mpmath.__version__,'mpmath_module_sha256':hashlib.sha256(mpath.read_bytes()).hexdigest(),
    'native_source_sha256':expected,'native_argv':NATIVE,'warning_policy':['ignore::UserWarning','ignore::SyntaxWarning'],
    'child_RV_exit':child.returncode,'child_RV_output':child_output[:65536].decode(errors='replace'),
    'child_RV_output_sha256':hashlib.sha256(child_output).hexdigest(),'child_RV_output_truncated':len(child_output)>65536,
    'subprocess_hash_randomization':'unavailable_native_fallback' if child.returncode else 'native_child_startup_available_execution_not_attested'}
print(IDENTITY+json.dumps(identity,sort_keys=True),flush=True)
print(START_MARKER,flush=True)
sys.argv=NATIVE
try:
    runpy.run_path('/testbed/bin/test',run_name='__main__')
except SystemExit as exc:
    code=exc.code if type(exc.code) is int else 0 if exc.code is None else 1
else:
    code=0
print('\\nP07_TEST_EXIT='+str(code),flush=True)
print(END_MARKER,flush=True)
raise SystemExit(code)
'''.replace("EXPECTED", repr(json.dumps(RUNNER_SOURCES))).replace("MPMATH_SHA", repr(expected_mpmath_sha256)) \
        .replace("NATIVE", repr(list(NATIVE_ARGV))).replace("IDENTITY", repr(IDENTITY_PREFIX)) \
        .replace("START_MARKER", repr(START)).replace("END_MARKER", repr(END))


def runtime_admission(image_id, receipt_dir, profile_path, actual_image, actual_base, actual_donor):
    profile_raw = profile_path.read_bytes().replace(b"\r\n", b"\n")
    if profile_raw != PROFILE.read_bytes().replace(b"\r\n", b"\n"):
        raise ValueError("runtime profile differs from reviewed checkout")
    profile = json.loads(profile_raw)
    raw = (receipt_dir / "sympy-runtime.json").read_bytes()
    report = json.loads(raw)
    parent = report.get("parent_transplant_report", {})
    labels = actual_image.get("Config", {}).get("Labels", {})
    base_layers = actual_base.get("RootFS", {}).get("Layers", [])
    parent_layers = parent.get("image_inspect", {}).get("RootFS", {}).get("Layers", [])
    layers = actual_image.get("RootFS", {}).get("Layers", [])
    cleanup = report.get("cleanup", [])
    if (not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
            or report.get("status") != "runtime_dependencies_qualified" or report.get("task") != TASK
            or report.get("base_image") != transplant.BASE or report.get("donor_image") != transplant.DONOR
            or report.get("base_commit") != transplant.COMMIT or report.get("profile_sha256") != sha(profile_raw)
            or actual_image.get("Id") != image_id or report.get("image_id") != image_id
            or any(item.get("Architecture") != "amd64" or item.get("Os") != "linux"
                   for item in (actual_image, actual_base, actual_donor))
            or labels.get("atomic.p07.sympy.base") != transplant.BASE
            or labels.get("atomic.p07.sympy.parent") != report.get("parent_image_id")
            or labels.get("atomic.p07.sympy.profile") != sha(profile_raw)
            or actual_image.get("RootFS") != report.get("image_inspect", {}).get("RootFS")
            or report.get("model_calls") != 0 or report.get("provider_credentials") != "none"
            or report.get("target_setup_or_tests_executed") is not False
            or report.get("cleanup_confirmed") is not True or report.get("cleanup_exit") != 0
            or not cleanup or any(item.get("confirmed") is not True for item in cleanup)
            or parent.get("status") != "transplant_stdlib_qualified" or parent.get("probe_compatible") is not True
            or parent.get("launcher_sha256") != sha(transplant.LAUNCHER_SOURCE.encode())
            or parent.get("probe_sha256") != sha(transplant.PROBE.encode())
            or not transplant.validate_probe(parent.get("observed", {}))
            or report.get("parent_transplant_report_sha256") != sha(
                (json.dumps(parent, sort_keys=True, indent=2) + "\n").encode())
            or parent.get("cleanup_confirmed") is not True or not parent.get("cleanup")
            or any(item.get("confirmed") is not True for item in parent.get("cleanup", []))
            or parent.get("base_image") != transplant.BASE or parent.get("donor_image") != transplant.DONOR
            or parent.get("base_commit") != transplant.COMMIT or parent.get("image_id") != report.get("parent_image_id")
            or parent.get("base_image_inspect", {}).get("Id") != actual_base.get("Id")
            or parent.get("donor_image_inspect", {}).get("Id") != actual_donor.get("Id")
            or actual_base.get("RootFS") != parent.get("base_image_inspect", {}).get("RootFS")
            or actual_donor.get("RootFS") != parent.get("donor_image_inspect", {}).get("RootFS")
            or not base_layers or parent_layers[:len(base_layers)] != base_layers or len(parent_layers) <= len(base_layers)
            or layers[:len(parent_layers)] != parent_layers or len(layers) <= len(parent_layers)
            or labels.get("atomic.p07.legacy.base") != transplant.BASE
            or labels.get("atomic.p07.legacy.donor") != transplant.DONOR
            or labels.get("atomic.p07.legacy.launcher") != sha(transplant.LAUNCHER_SOURCE.encode())
            or report.get("launcher_sha256") != sha(transplant.LAUNCHER_SOURCE.encode())
            or not transplant.validate_probe(report.get("runtime_probe", {}))):
        raise ValueError("runtime, image ancestry, isolated stdlib or completed cleanup admission mismatch")
    wheel = report.get("wheel", {})
    expected = profile["dependency"]
    if (any(wheel.get(key) != expected.get(key) for key in ("name", "version", "filename", "url", "sha256", "wheel_metadata_sha256"))
            or not re.fullmatch(r"[0-9a-f]{64}", wheel.get("module_member_sha256", ""))
            or any(not isinstance(report.get(key), dict) for key in ("before", "after"))
            or report["before"] != report["after"] or report["before"] != parent.get("after")
            or parent.get("before") != parent.get("after")
            or report.get("mpmath_probe", {}).get("version") != "1.2.1"
            or report.get("mpmath_probe", {}).get("module_sha256") != wheel["module_member_sha256"]
            or report.get("mpmath_probe", {}).get("prefix") != transplant.PREFIX
            or report.get("mpmath_probe", {}).get("executable") != transplant.PREFIX + "/bin/python3.6"
            or not report.get("mpmath_probe", {}).get("module_path", "").startswith(
                transplant.PREFIX + "/lib/python3.6/site-packages/mpmath/")):
        raise ValueError("verified wheel, dependency import or source identity mismatch")
    expected_manifest = profile["task"]
    if (expected_manifest["base_commit"] != transplant.COMMIT or expected_manifest["official_base_image"] != transplant.BASE
            or profile["transplant"]["source_git_lf_sha256"] != sha(Path(transplant.__file__).read_bytes().replace(b"\r\n", b"\n"))):
        raise ValueError("profile target identity mismatch")
    return {"image_id": image_id, "profile_sha256": sha(profile_raw), "runtime_receipt_sha256": sha(raw),
            "source": report["after"], "mpmath_module_sha256": wheel["module_member_sha256"],
            "bootstrap_sha256": sha(bootstrap_source(wheel["module_member_sha256"]).encode()), "profile": profile}


def validate_identity(identity, admitted):
    if (identity.get("version_info") != [3, 6, 15] or identity.get("prefix") != transplant.PREFIX
            or identity.get("executable") != transplant.PREFIX + "/bin/python3.6" or identity.get("isolated") != 1
            or identity.get("sympy_module") != "/testbed/sympy/__init__.py"
            or identity.get("mpmath_version") != "1.2.1"
            or identity.get("mpmath_module_sha256") != admitted["mpmath_module_sha256"]
            or not identity.get("mpmath_module", "").startswith(transplant.PREFIX + "/lib/python3.6/site-packages/mpmath/")
            or identity.get("native_source_sha256") != RUNNER_SOURCES or identity.get("native_argv") != list(NATIVE_ARGV)
            or identity.get("warning_policy") != ["ignore::UserWarning", "ignore::SyntaxWarning"]
            or type(identity.get("child_RV_exit")) is not int
            or not isinstance(identity.get("child_RV_output"), str)
            or identity.get("child_RV_output_truncated") is not False
            or sha(identity.get("child_RV_output", "").encode()) != identity.get("child_RV_output_sha256")
            or identity.get("subprocess_hash_randomization") != ("unavailable_native_fallback" if identity["child_RV_exit"]
                else "native_child_startup_available_execution_not_attested")):
        raise ValueError("fresh native runner identity mismatch")


def classify_native(task, text, process_exit, parser, spec, admitted):
    if (text.count(START) != 1 or text.count(END) != 1 or text.index(START) >= text.index(END)):
        raise ValueError("missing or duplicate native test markers")
    identities = re.findall(r"^" + re.escape(IDENTITY_PREFIX) + r"(.*)$", text, re.MULTILINE)
    if len(identities) != 1 or text.index(IDENTITY_PREFIX) >= text.index(START):
        raise ValueError("missing or duplicate runner identity")
    identity = json.loads(identities[0])
    validate_identity(identity, admitted)
    body = text.split(START, 1)[1].split(END, 1)[0]
    exits = re.findall(r"^P07_TEST_EXIT=(\d+)$", body, re.MULTILINE)
    clean = oracle.ANSI.sub("", body)
    if (exits != [str(process_exit)] or process_exit not in {0, 1}
            or len(re.findall(r"^\s*(?:=+\s*)?tests finished:[^\n]*\bin [0-9.]+ seconds[^\n]*$", clean, re.MULTILINE)) != 1):
        raise ValueError("native test completion or exit identity mismatch")
    tests, diagnostics = oracle.parse_observations(task, body, parser, spec)
    exceptions = oracle.test_body_exception_receipts(task, body, tests)
    return {**oracle.classify(task, tests, completed=True, exit_code=process_exit, requirement_exceptions=exceptions),
            "tests": tests, "parser_diagnostics": diagnostics, "test_body_exception_receipts": exceptions,
            "runner_identity": identity}


def execute(args, *, timeout=60, log=None):
    env = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}}
    if log is not None:
        with log.open("wb") as stream:
            return subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, check=False, env=env)
    return subprocess.run(args, capture_output=True, timeout=timeout, check=False, env=env)


def checked(args, *, timeout=60):
    process = execute(args, timeout=timeout)
    if process.returncode:
        raise RuntimeError("isolated worker command failed: " + args[1])
    return process.stdout.decode().strip()


def cleanup(name):
    try:
        process = execute(["docker", "rm", "-f", name], timeout=30)
        absent = execute(["docker", "container", "ls", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"], timeout=15)
        return {"cleanup_exit": process.returncode, "cleanup_confirmed": absent.returncode == 0 and not absent.stdout.strip()}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"cleanup_exit": None, "cleanup_confirmed": False, "cleanup_error_type": type(exc).__name__}


def grade(task, spec, parser, admitted, patch, label, output, timeout):
    name = "p07-sympy13971-" + uuid.uuid4().hex[:20]
    row = {"label": label, "status": "infrastructure_error", "verified_success": False,
           "patch_sha256": sha(patch.encode()), "bootstrap_sha256": admitted["bootstrap_sha256"], "container_name": name}
    log = output / (label + ".log")
    try:
        paths = patch_paths(patch) if patch else set()
        if paths & patch_paths(task["test_patch"]) or any(protected_test_path(path) for path in paths):
            raise ValueError("source patch crosses protected test boundary")
        checked(["docker", "create", "--name", name, "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                 "--memory", "6g", "--cpus", "2", "--pids-limit", "256", "--entrypoint", "/bin/sleep", admitted["image_id"], "infinity"])
        config = json.loads(checked(["docker", "inspect", name]))[0]
        host = config["HostConfig"]
        if (config.get("Image") != admitted["image_id"] or config.get("Mounts") or host.get("Binds") or host.get("Privileged")
                or host.get("NetworkMode") != "none" or host.get("CapDrop") != ["ALL"]
                or "no-new-privileges" not in host.get("SecurityOpt", []) or host.get("Memory") != 6 * 1024**3
                or host.get("NanoCpus") != 2_000_000_000 or host.get("PidsLimit") != 256):
            raise ValueError("oracle container isolation mismatch")
        row["isolation"] = {"network": "none", "host_mounts": [], "caps": "ALL dropped", "no_new_privileges": True,
                            "memory_bytes": 6 * 1024**3, "cpus": 2, "pids_limit": 256}
        checked(["docker", "start", name])
        git = ["docker", "exec", name, "git", "-C", "/testbed"]
        source = {"head": checked([*git, "rev-parse", "HEAD"]), "diff": checked([*git, "diff", "--binary", "HEAD"]),
                  "status": checked([*git, "status", "--porcelain", "--untracked-files=all"])}
        if source != admitted["source"]:
            raise ValueError("fresh image target source differs from runtime receipt")
        launcher = checked(["docker", "exec", name, "cat", transplant.LAUNCHER])
        if launcher + "\n" != transplant.LAUNCHER_SOURCE:
            raise ValueError("fresh private launcher source mismatch")
        checked([*git, "reset", "--hard", transplant.COMMIT])
        checked([*git, "clean", "-fd"])
        row["base_verified"] = checked([*git, "rev-parse", "HEAD"])
        if row["base_verified"] != transplant.COMMIT:
            raise ValueError("target base identity mismatch")
        for kind, content in (("test", task["test_patch"]), (label, patch)):
            if not content:
                continue
            path = output / (label + "-" + kind + ".patch")
            path.write_bytes(content.encode())
            inside = "/tmp/p07-" + kind + ".patch"
            checked(["docker", "cp", str(path), name + ":" + inside])
            checked([*git, "apply", "--check", inside])
            checked([*git, "apply", inside])
        before = checked([*git, "diff", "--binary", "HEAD"])
        bootstrap = bootstrap_source(admitted["mpmath_module_sha256"])
        process = execute(["docker", "exec", "-w", "/testbed", name, transplant.LAUNCHER, "-c", bootstrap], timeout=timeout, log=log)
        row["process_exit"] = process.returncode
        after = checked([*git, "diff", "--binary", "HEAD"])
        if before != after:
            raise ValueError("native tests changed tracked source or protected tests")
        if log.stat().st_size > LOG_LIMIT:
            raise ValueError("native output exceeds bounded audit size")
        row.update(classify_native(task, log.read_text(errors="replace"), process.returncode, parser, spec, admitted))
    except (OSError, ValueError, TypeError, KeyError, IndexError, RuntimeError, subprocess.SubprocessError) as exc:
        row.update(error_type=type(exc).__name__, error=str(exc)[:500])
    finally:
        row.update(cleanup(name))
        if not row["cleanup_confirmed"]:
            row.update(status="infrastructure_error", verified_success=False, cleanup_uncertain=True)
        if log.exists():
            row["log_sha256"] = sha(log.read_bytes()) if log.stat().st_size <= LOG_LIMIT else "unavailable_output_bound_exceeded"
        save(output / (label + ".json"), row)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--runtime-receipt", type=Path, required=True)
    parser.add_argument("--runtime-profile", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=ROOT / "benchmarks/stage4/corpus-p07-v2.json")
    parser.add_argument("--controls", type=Path, default=ROOT / "benchmarks/stage4/p07-controls-v5.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    if platform.system() != "Linux" or not 10 <= args.timeout <= 600 or args.output.exists():
        raise ValueError("approved Linux worker, bounded timeout and fresh output required")
    args.output.mkdir(parents=True)
    report = {"instance_id": TASK, "status": "infrastructure_error", "calibrated": False, "model_calls": 0,
              "provider_credentials": "none", "harness": oracle.HARNESS_VERSION, "candidate_scope": "not_supported"}
    try:
        if importlib.metadata.version("swebench") != oracle.HARNESS_VERSION:
            raise ValueError("unreviewed independent evaluator version")
        from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS
        from swebench.harness.log_parsers import MAP_REPO_TO_PARSER
        from swebench.harness.test_spec.test_spec import make_test_spec

        task = next(item for item in load_manifest(args.manifest)["tasks"] if item["instance_id"] == TASK)
        if task["base_commit"] != transplant.COMMIT or task["repo"] != "sympy/sympy":
            raise ValueError("task source identity mismatch")
        spec = make_test_spec(task, namespace="swebench")
        native = MAP_REPO_VERSION_TO_SPECS[task["repo"]][task["version"]]["test_cmd"]
        if native != "PYTHONWARNINGS='ignore::UserWarning,ignore::SyntaxWarning' bin/test -C --verbose":
            raise ValueError("official native command changed")
        control = json.loads(args.controls.read_bytes())["tasks"][TASK]
        if control.get("gold_sha256") != sha(task["patch"].encode()) or task["patch"].count(control["old"]) != 1:
            raise ValueError("wrong control is not bound to this gold source")
        wrong = task["patch"].replace(control["old"], control["new"])
        if wrong == task["patch"] or not control.get("rationale"):
            raise ValueError("negative control lacks a reviewed source mutation")
        actual = json.loads(checked(["docker", "image", "inspect", args.image]))[0]
        base = json.loads(checked(["docker", "image", "inspect", transplant.BASE]))[0]
        donor = json.loads(checked(["docker", "image", "inspect", transplant.DONOR]))[0]
        admitted = runtime_admission(args.image, args.runtime_receipt, args.runtime_profile, actual, base, donor)
        profile = admitted["profile"]
        if (profile["oracle"]["FAIL_TO_PASS"] != task["FAIL_TO_PASS"] or profile["oracle"]["PASS_TO_PASS"] != task["PASS_TO_PASS"]
                or len(task["FAIL_TO_PASS"]) + len(task["PASS_TO_PASS"]) != 103
                or profile["oracle"]["test_patch_sha256"] != sha(task["test_patch"].encode())):
            raise ValueError("complete oracle obligation differs from frozen profile")
        report.update(image_id=args.image, base_image=transplant.BASE, donor_image=transplant.DONOR,
            runtime_profile_sha256=admitted["profile_sha256"], runtime_receipt_sha256=admitted["runtime_receipt_sha256"],
            bootstrap_sha256=admitted["bootstrap_sha256"], native_source_sha256=RUNNER_SOURCES,
            manifest_sha256=sha(args.manifest.read_bytes()), controls_sha256=sha(args.controls.read_bytes()),
            native_command=native, native_argv=list(NATIVE_ARGV), required_tests=103)
        (args.output / "bootstrap.py").write_bytes(bootstrap_source(admitted["mpmath_module_sha256"]).encode())
        rows = [grade(task, spec, MAP_REPO_TO_PARSER[task["repo"]], admitted, patch, label, args.output, args.timeout)
                for label, patch in (("base", ""), ("gold", task["patch"]), ("wrong", wrong))]
        report.update(status="completed", rows=rows, calibrated=oracle.calibration_decision(rows))
    except (OSError, ValueError, TypeError, RuntimeError, KeyError, IndexError, StopIteration, subprocess.SubprocessError) as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    save(args.output / "calibration.json", report)
    print(json.dumps({key: report[key] for key in ("instance_id", "status", "calibrated", "model_calls")}))
    return 0 if report["calibrated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
