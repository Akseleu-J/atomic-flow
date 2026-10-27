"""Provision Requests3362 legacy wheel closure; no target or plugin execution.

The qualified scope is private CPython ABI plus installed bytes, not pytest
startup, HTTPBin health, certificate acceptance or benchmark calibration.
"""
from __future__ import annotations

import argparse
import email
import io
import json
import os
import platform
import re
import subprocess
import uuid
import zipfile
from pathlib import Path, PurePosixPath

import p07_legacy_transplant as transplant
from p07_sympy13971_image import fetch
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "benchmarks/stage4/p07-requests-legacy-profile.json"
TASK = "psf__requests-3362"
BASE = "swebench/sweb.eval.x86_64.psf_1776_requests-3362@sha256:c5dc4ba64a2d97d6cff6b82fe9c0561589df13f23df6883d05e99b630942bbd1"
COMMIT = "36453b95b13079296776d11b09cab2567ea3e703"
DONOR = transplant.DONOR
LIMIT = 4 * 1024**2
MARKERS = {"implementation_name": "cpython", "implementation_version": "3.6.15", "python_version": "3.6",
    "python_full_version": "3.6.15", "platform_machine": "x86_64", "platform_system": "Linux",
    "platform_release": "", "platform_version": "", "platform_python_implementation": "CPython",
    "sys_platform": "linux", "os_name": "posix", "extra": ""}
PLATFORMS = ["manylinux1_x86_64", "linux_x86_64"]
TAGS = set(cpython_tags((3, 6), ["cp36m"], PLATFORMS)) | set(compatible_tags((3, 6), "cp36", PLATFORMS))


def sha(raw):
    return transplant.sha(raw)


def save(path, value):
    transplant.save(path, value)


def frozen_profile(path):
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    if raw != PROFILE.read_bytes().replace(b"\r\n", b"\n"):
        raise ValueError("profile differs from reviewed checkout")
    profile = json.loads(raw)
    target = next(item for item in profile["tasks"] if item["instance_id"] == TASK)
    if target["base_image_digest"] != BASE or target["base_commit"] != COMMIT or len(profile["wheels"]) != 13:
        raise ValueError("fixed target or dependency cardinality differs")
    return raw, profile


def verified_wheel(record, raw):
    if sha(raw) != record["sha256"] or len(raw) != record["bytes"] or len(raw) > LIMIT:
        raise ValueError("wheel bytes differ from reviewed lock")
    name, version, _, tags = parse_wheel_filename(record["filename"])
    if (canonicalize_name(record["name"]) != name or str(version) != record["version"] or not tags & TAGS):
        raise ValueError("wheel name, version or CPython3.6/Linux ABI mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if len(entries) > 4000 or len(names) != len(set(names)) or sum(item.file_size for item in entries) > 32 * 1024**2:
            raise ValueError("wheel archive bounds or duplicate member mismatch")
        for item in entries:
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in item.filename or ":" in item.filename
                    or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("unsafe wheel path")
        metadata = [item for item in entries if item.filename.endswith(".dist-info/METADATA")]
        if len(metadata) != 1 or metadata[0].file_size > 1024**2:
            raise ValueError("ambiguous wheel metadata")
        metadata_raw = archive.read(metadata[0])
        message = email.message_from_bytes(metadata_raw)
        requirements = message.get_all("Requires-Dist", [])
        if (canonicalize_name(message.get("Name", "")) != name or message.get("Version") != record["version"]
                or requirements != record["requires_dist"] or message.get("Requires-Python") != record["requires_python"]):
            raise ValueError("actual wheel METADATA differs from reviewed profile")
        if message.get("Requires-Python") and not SpecifierSet(message["Requires-Python"]).contains("3.6.15"):
            raise ValueError("dependency refuses fixed interpreter")
        inventory = {}
        for item in entries:
            if item.is_dir() or ".data/" in item.filename:
                continue
            if ".dist-info/" in item.filename and item.filename != metadata[0].filename:
                continue
            inventory[item.filename] = sha(archive.read(item))
        return {"name": record["name"], "version": record["version"], "filename": record["filename"],
                "sha256": record["sha256"], "metadata_sha256": sha(metadata_raw),
                "requires_dist": requirements, "installed_files": inventory}


def closure(profile, verified):
    pins = {canonicalize_name(name): value for name, value in profile["pins"].items()}
    if len(pins) != 13 or len(verified) != 13 or {canonicalize_name(item["name"]): item["version"] for item in verified} != pins:
        raise ValueError("wheel closure identity/cardinality mismatch")
    edges = []
    for item in verified:
        for declared in item["requires_dist"]:
            requirement = Requirement(declared)
            if requirement.marker is not None and not requirement.marker.evaluate(MARKERS):
                continue
            key = canonicalize_name(requirement.name)
            if requirement.url or requirement.extras or key not in pins or not requirement.specifier.contains(pins[key]):
                raise ValueError("active dependency outside fixed closed wheel set")
            edges.append((canonicalize_name(item["name"]), key, str(requirement.specifier)))
    expected = [(canonicalize_name(edge["from"]), canonicalize_name(Requirement(edge["requires"]).name),
                 str(Requirement(edge["requires"]).specifier)) for edge in profile["closure"]["active_edges"]]
    if sorted(edges) != sorted(expected):
        raise ValueError("active dependency closure differs from reviewed profile")
    return sorted(edges)


def requirements(profile):
    return "".join(item["name"] + "==" + item["version"] + " --hash=sha256:" + item["sha256"] + "\n"
                   for item in sorted(profile["wheels"], key=lambda item: canonicalize_name(item["name"])))


def dockerfile():
    return (transplant.dockerfile().replace(transplant.BASE, BASE)
            + "COPY wheels/ /opt/p07-requests-wheels/\n"
            + "COPY requirements.lock /opt/p07-requests-wheels/requirements.lock\n"
            + "RUN --network=none " + transplant.LAUNCHER
            + " -m pip install --no-index --no-deps --no-compile --ignore-installed --require-hashes"
            + " --find-links=/opt/p07-requests-wheels -r /opt/p07-requests-wheels/requirements.lock\n")


def inventory_source(files):
    return '''import hashlib,json,pathlib,sys
expected=json.loads(EXPECTED)
root=pathlib.Path('/opt/p07-python36/lib/python3.6/site-packages')
observed={}
for relative,digest in sorted(expected.items()):
    path=root/relative
    if path.is_symlink() or not path.is_file() or not str(path.resolve()).startswith(str(root)+'/'):
        raise RuntimeError('installed dependency path mismatch')
    actual=hashlib.sha256(path.read_bytes()).hexdigest()
    if actual!=digest:
        raise RuntimeError('installed dependency bytes mismatch')
    observed[relative]=actual
print(json.dumps({'files':observed,'prefix':sys.prefix,'version_info':list(sys.version_info[:3]),'target_or_plugins_imported':False},sort_keys=True))
'''.replace("EXPECTED", repr(json.dumps(files, sort_keys=True)))


def prepare(profile, output, *, downloader=fetch):
    if output.exists():
        raise ValueError("fresh Requests runtime context required")
    raw_profile, config = frozen_profile(profile)
    wheels = []
    downloads = []
    for record in config["wheels"]:
        publication = json.loads(downloader(record["primary_metadata_url"]))
        selected = [item for item in publication["urls"] if item["filename"] == record["filename"]]
        if (len(selected) != 1 or selected[0]["url"] != record["url"]
                or selected[0]["digests"]["sha256"] != record["sha256"]):
            raise ValueError("primary PyPI publication differs")
        raw = downloader(record["url"])
        wheels.append(verified_wheel(record, raw))
        downloads.append((record["filename"], raw))
    edges = closure(config, wheels)
    files = {}
    for wheel in wheels:
        if files.keys() & wheel["installed_files"].keys():
            raise ValueError("overlapping wheel installation paths")
        files.update(wheel["installed_files"])
    output.mkdir(parents=True)
    (output / "wheels").mkdir()
    for filename, raw in downloads:
        (output / "wheels" / filename).write_bytes(raw)
    (output / "requirements.lock").write_bytes(requirements(config).encode())
    (output / "Dockerfile").write_bytes(dockerfile().encode())
    (output / "python36").write_bytes(transplant.LAUNCHER_SOURCE.encode())
    report = {"status": "prepared_not_built", "task": TASK, "base_image": BASE, "donor_image": DONOR,
              "base_commit": COMMIT, "profile_sha256": sha(raw_profile), "wheels": wheels, "installed_files": files,
              "active_edges": edges, "dockerfile_sha256": sha(dockerfile().encode()),
              "launcher_sha256": sha(transplant.LAUNCHER_SOURCE.encode()), "probe_sha256": sha(transplant.PROBE.encode()),
              "requirements_sha256": sha(requirements(config).encode()), "model_calls": 0,
              "target_setup_or_tests_executed": False, "target_or_plugins_imported": False, "calibrated": False,
              "remaining_gates": ["pytest/plugin startup", "HTTPBin HTTP/CA-HTTPS health", "fresh reviewed CN-only certificate",
                                  "hermetic TARPIT topology", "prospective wrong control", "full base/gold/wrong calibration"]}
    save(output / "preparation.json", report)
    return report


def validate_context(context):
    raw_profile, config = frozen_profile(PROFILE)
    prepared = json.loads((context / "preparation.json").read_bytes())
    wheels = [verified_wheel(record, (context / "wheels" / record["filename"]).read_bytes()) for record in config["wheels"]]
    edges = closure(config, wheels)
    files = {}
    for wheel in wheels:
        if files.keys() & wheel["installed_files"].keys():
            raise ValueError("overlapping wheel paths")
        files.update(wheel["installed_files"])
    if (prepared.get("status") != "prepared_not_built"
            or prepared.get("dockerfile_sha256") != sha(dockerfile().encode())
            or prepared.get("launcher_sha256") != sha(transplant.LAUNCHER_SOURCE.encode())
            or prepared.get("probe_sha256") != sha(transplant.PROBE.encode())
            or prepared.get("requirements_sha256") != sha(requirements(config).encode())
            or prepared.get("profile_sha256") != sha(raw_profile) or prepared.get("wheels") != wheels
            or prepared.get("installed_files") != files or prepared.get("active_edges") != [list(edge) for edge in edges]
            or prepared.get("task") != TASK or prepared.get("base_image") != BASE or prepared.get("donor_image") != DONOR
            or prepared.get("base_commit") != COMMIT or prepared.get("model_calls") != 0
            or prepared.get("target_setup_or_tests_executed") is not False or prepared.get("target_or_plugins_imported") is not False
            or (context / "Dockerfile").read_bytes() != dockerfile().encode()
            or (context / "python36").read_bytes() != transplant.LAUNCHER_SOURCE.encode()
            or (context / "requirements.lock").read_bytes() != requirements(config).encode()):
        raise ValueError("prepared Requests context changed")
    return prepared


STDIN_BOOTSTRAP = "import sys; exec(compile(sys.stdin.buffer.read(), '<p07-inventory>', 'exec'))"


def run_inventory(name, source, runner):
    """Stream host-owned code into the private interpreter without filesystem writes."""
    return runner(["docker", "exec", "-i", name, transplant.LAUNCHER, "-c", STDIN_BOOTSTRAP],
                  60, input_bytes=source.encode())


def command_failure(args, process):
    """Keep bounded diagnostics for fixed, credential-free worker commands."""
    return {"command": args[:2], "exit_code": process.returncode,
            "stdout": process.stdout[:2000].decode(errors="replace"),
            "stderr": process.stderr[:2000].decode(errors="replace"),
            "stdout_sha256": sha(process.stdout), "stderr_sha256": sha(process.stderr),
            "stdout_truncated": len(process.stdout) > 2000, "stderr_truncated": len(process.stderr) > 2000}


def build(context, output):
    if platform.system() != "Linux" or output.exists():
        raise ValueError("approved Linux Docker worker and fresh output required")
    prepared = validate_context(context)
    if ((context / "Dockerfile").read_bytes() != dockerfile().encode()
            or (context / "python36").read_bytes() != transplant.LAUNCHER_SOURCE.encode()):
        raise ValueError("transplant context differs from fixed reviewed bytes")
    output.mkdir(parents=True)
    report = {**prepared, "status": "runtime_unavailable", "base_image": BASE, "donor_image": DONOR,
              "base_commit": COMMIT, "prefix": transplant.PREFIX, "launcher": transplant.LAUNCHER,
              "model_calls": 0, "provider_credentials": "none", "target_setup_or_tests_executed": False,
              "calibrated": False, "probe_sha256": sha(transplant.PROBE.encode()),
              "dockerfile_sha256": sha(dockerfile().encode()), "launcher_sha256": sha(transplant.LAUNCHER_SOURCE.encode())}
    containers = []

    def execute(args, timeout=60, *, input_bytes=None):
        clean = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}}
        return subprocess.run(args, input=input_bytes, capture_output=True, timeout=timeout, check=False, env=clean)

    def checked(args, timeout=60):
        process = execute(args, timeout)
        if process.returncode:
            report["command_error"] = command_failure(args, process)
            raise RuntimeError("transplant command failed: " + args[1])
        return process.stdout.decode().strip()

    def inspect_image(image):
        value = json.loads(checked(["docker", "image", "inspect", image]))[0]
        if (value.get("Architecture") != "amd64" or value.get("Os") != "linux"
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", value.get("Id", ""))):
            raise ValueError("transplant image identity/platform mismatch")
        return value

    def create(image):
        name = "p07-transplant-" + uuid.uuid4().hex[:24]
        containers.append(name)
        checked(["docker", "create", "--name", name, "--network", "none", "--read-only", "--user", "65534:65534",
                 "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "1g", "--cpus", "2",
                 "--pids-limit", "128", "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m,mode=1777",
                 "--workdir", "/tmp", "--entrypoint", "/bin/sleep", image, "infinity"])
        config = json.loads(checked(["docker", "inspect", name]))[0]
        host = config["HostConfig"]
        if (config.get("Image") != image or config.get("Mounts") or host.get("Binds") or host.get("Privileged")
                or host.get("NetworkMode") != "none" or not host.get("ReadonlyRootfs")
                or host.get("CapDrop") != ["ALL"] or "no-new-privileges" not in host.get("SecurityOpt", [])
                or host.get("Memory") != 1024**3 or host.get("NanoCpus") != 2_000_000_000
                or host.get("PidsLimit") != 128 or config["Config"].get("User") != "65534:65534"):
            raise ValueError("transplant probe container isolation mismatch")
        checked(["docker", "start", name])
        return name

    def source(name):
        # Git may reject a non-root probe reading a root-owned checkout unless
        # that one exact path is explicitly safe. No hooks/config are executed.
        git = ["docker", "exec", name, "git", "-c", "safe.directory=/testbed", "-C", "/testbed"]
        checked([*git, "cat-file", "-e", COMMIT + "^{commit}"])
        return {"head": checked([*git, "rev-parse", "HEAD"]),
                "diff": checked([*git, "diff", "--binary", "HEAD"]),
                "status": checked([*git, "status", "--porcelain", "--untracked-files=all"])}

    try:
        for key, image in (("base", BASE), ("donor", DONOR)):
            process = execute(["docker", "pull", image], 600)
            (output / (key + "-pull.stdout")).write_bytes(process.stdout)
            (output / (key + "-pull.stderr")).write_bytes(process.stderr)
            if process.returncode:
                raise RuntimeError(key + " immutable image pull failed")
            report[key + "_image_inspect"] = inspect_image(image)
            accepted = {image, image.removeprefix("library/"), "docker.io/" + image}
            if not set(report[key + "_image_inspect"].get("RepoDigests", [])) & accepted:
                raise ValueError(key + " image digest mismatch")
        before = create(report["base_image_inspect"]["Id"])
        report["before"] = source(before)
        tag = "p07-legacy-transplant:" + uuid.uuid4().hex
        building = execute(["docker", "build", "--network", "none", "--tag", tag,
            "--label", "atomic.p07.legacy.base=" + BASE, "--label", "atomic.p07.legacy.donor=" + DONOR,
            "--label", "atomic.p07.legacy.launcher=" + sha(transplant.LAUNCHER_SOURCE.encode()),
            "--label", "atomic.p07.requests.profile=" + prepared["profile_sha256"], str(context)], 900)
        (output / "build.stdout").write_bytes(building.stdout)
        (output / "build.stderr").write_bytes(building.stderr)
        if building.returncode:
            raise RuntimeError("private-prefix transplant build failed")
        image = inspect_image(tag)
        base_layers = report["base_image_inspect"]["RootFS"]["Layers"]
        layers = image["RootFS"]["Layers"]
        if not base_layers or layers[:len(base_layers)] != base_layers or len(layers) <= len(base_layers):
            raise ValueError("transplant does not preserve official base ancestry")
        report.update(image_id=image["Id"], image_inspect=image)
        after = create(image["Id"])
        report["after"] = source(after)
        if report["before"] != report["after"]:
            raise ValueError("private runtime transplant changed target source")
        process = execute(["docker", "exec", after, transplant.LAUNCHER, "-c", transplant.PROBE], 45)
        (output / "probe.stdout").write_bytes(process.stdout)
        (output / "probe.stderr").write_bytes(process.stderr)
        report.update(process_exit=process.returncode, stdout_sha256=sha(process.stdout), stderr_sha256=sha(process.stderr))
        if process.returncode:
            raise RuntimeError("private-prefix runtime startup failed; original logs retained")
        report["observed"] = json.loads(process.stdout)
        compatible = transplant.validate_probe(report["observed"])
        report.update(status="runtime_probe_incompatibility", probe_compatible=compatible)
        if not compatible:
            raise ValueError("private runtime ABI qualification failed")
        inventory_code = inventory_source(prepared["installed_files"])
        (output / "inventory.py").write_bytes(inventory_code.encode())
        process = run_inventory(after, inventory_code, execute)
        (output / "inventory.stdout").write_bytes(process.stdout)
        (output / "inventory.stderr").write_bytes(process.stderr)
        report["inventory_probe_sha256"] = sha(inventory_code.encode())
        report["inventory_transfer"] = "docker_exec_stdin_no_filesystem_write"
        report["inventory_stdin_bootstrap_sha256"] = sha(STDIN_BOOTSTRAP.encode())
        report["inventory_exit"] = process.returncode
        report["inventory_stdout_sha256"] = sha(process.stdout)
        report["inventory_stderr_sha256"] = sha(process.stderr)
        if process.returncode:
            raise RuntimeError("installed dependency byte inventory failed")
        inventory = json.loads(process.stdout)
        if (inventory.get("files") != prepared["installed_files"] or inventory.get("prefix") != transplant.PREFIX
                or inventory.get("version_info") != [3, 6, 15] or inventory.get("target_or_plugins_imported") is not False):
            raise ValueError("installed dependencies differ from publisher wheels")
        report["inventory"] = inventory
        if source(after) != report["after"]:
            raise ValueError("qualification changed target source")
        report["status"] = "runtime_dependencies_qualified"
    except (OSError, ValueError, TypeError, KeyError, IndexError, RuntimeError, subprocess.SubprocessError) as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    finally:
        report["cleanup"] = []
        for name in containers:
            try:
                process = execute(["docker", "rm", "-f", name], 30)
                absent = execute(["docker", "container", "ls", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"], 15)
                confirmed = absent.returncode == 0 and not absent.stdout.strip()
                report["cleanup"].append({"container": name, "exit": process.returncode, "confirmed": confirmed})
            except (OSError, subprocess.SubprocessError) as exc:
                report["cleanup"].append({"container": name, "confirmed": False, "error_type": type(exc).__name__})
        report["cleanup_confirmed"] = all(item["confirmed"] for item in report["cleanup"])
        if not report["cleanup_confirmed"]:
            report.update(status="cleanup_uncertain", probe_compatible=False)
        save(output / "requests-runtime.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    preparing = sub.add_parser("prepare")
    preparing.add_argument("--profile", type=Path, default=PROFILE)
    preparing.add_argument("--output", type=Path, required=True)
    building = sub.add_parser("build")
    building.add_argument("--context", type=Path, required=True)
    building.add_argument("--output", type=Path, required=True)
    arguments = vars(parser.parse_args())
    action = arguments.pop("action")
    result = prepare(**arguments) if action == "prepare" else build(**arguments)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] in {"prepared_not_built", "runtime_dependencies_qualified"} else 1)
