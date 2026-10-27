"""Provision one verified mpmath wheel in a qualified private Python runtime."""
from __future__ import annotations

import argparse
import email
import io
import json
import os
import platform
import re
import subprocess
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

import p07_legacy_transplant as transplant
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "benchmarks/stage4/p07-sympy13971-runtime-profile.json"
LIMIT = 4 * 1024 * 1024
MPMATH_PROBE = r'''
import hashlib,json,pathlib,sys
import mpmath
path=pathlib.Path(mpmath.__file__)
print(json.dumps({"version":mpmath.__version__,"module_path":str(path),
    "module_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
    "prefix":sys.prefix,"executable":sys.executable},sort_keys=True))
'''


def fetch(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in {"pypi.org", "files.pythonhosted.org"}
            or parsed.username or parsed.password or parsed.port not in {None, 443} or parsed.query or parsed.fragment):
        raise ValueError("unreviewed dependency source")
    with urllib.request.urlopen(url, timeout=60) as response:
        if urllib.parse.urlsplit(response.geturl()).hostname != parsed.hostname:
            raise ValueError("dependency redirect changed authority")
        raw = response.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("dependency download too large")
    return raw


def parent_report(directory):
    raw = (directory / "legacy-transplant.json").read_bytes()
    report = json.loads(raw)
    if (report.get("status") != "transplant_stdlib_qualified" or report.get("probe_compatible") is not True
            or report.get("cleanup_confirmed") is not True or len(report.get("cleanup", [])) != 2
            or any(item.get("confirmed") is not True for item in report["cleanup"])
            or report.get("base_image") != transplant.BASE or report.get("donor_image") != transplant.DONOR
            or report.get("base_commit") != transplant.COMMIT
            or report.get("launcher_sha256") != transplant.sha(transplant.LAUNCHER_SOURCE.encode())
            or report.get("probe_sha256") != transplant.sha(transplant.PROBE.encode())
            or not transplant.validate_probe(report.get("observed", {}))
            or report.get("before") != report.get("after")
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", report.get("image_id", ""))
            or report.get("image_inspect", {}).get("Id") != report["image_id"]
            or report.get("model_calls") != 0 or report.get("target_setup_or_tests_executed") is not False):
        raise ValueError("parent transplant was not independently qualified")
    return raw, report


def verified_wheel(record, raw):
    if (record.get("name") != "mpmath" or record.get("version") != "1.2.1"
            or record.get("filename") != "mpmath-1.2.1-py3-none-any.whl"
            or transplant.sha(raw) != record.get("sha256") or len(raw) > LIMIT):
        raise ValueError("mpmath wheel identity differs")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > 3000 or sum(item.file_size for item in entries) > 16 * 1024 * 1024:
            raise ValueError("mpmath archive bounds exceeded")
        for item in entries:
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in item.filename or ":" in item.filename
                    or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("unsafe wheel path")
        metadata = [item for item in entries if item.filename.endswith(".dist-info/METADATA")]
        if len(metadata) != 1 or metadata[0].file_size > 1024 * 1024:
            raise ValueError("ambiguous mpmath metadata")
        data = archive.read(metadata[0])
        message = email.message_from_bytes(data)
        if (message.get("Name", "").lower() != "mpmath" or message.get("Version") != "1.2.1"
                or transplant.sha(data) != record.get("wheel_metadata_sha256")):
            raise ValueError("mpmath metadata identity differs")
        marker_env = {"python_version": "3.6", "python_full_version": "3.6.15", "extra": "",
                      "sys_platform": "linux", "platform_system": "Linux", "implementation_name": "cpython"}
        for declared in message.get_all("Requires-Dist", []):
            requirement = Requirement(declared)
            if requirement.marker is None or requirement.marker.evaluate(marker_env):
                raise ValueError("active unreviewed mpmath runtime dependency")
        names = [item.filename for item in entries]
        if names.count("mpmath/__init__.py") != 1:
            raise ValueError("mpmath module identity ambiguous")
        return transplant.sha(archive.read("mpmath/__init__.py"))


def parent_tag(image_id):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("immutable parent ID required")
    return "p07-qualified-transplant:" + image_id.split(":", 1)[1]


def dockerfile(image_id):
    return (f"FROM {parent_tag(image_id)}\nUSER root\n"
            "COPY mpmath-1.2.1-py3-none-any.whl requirements.lock /opt/p07-mpmath/\n"
            f"RUN --network=none {transplant.LAUNCHER} -m pip install --no-index --no-deps --no-compile "
            "--require-hashes --find-links=/opt/p07-mpmath -r /opt/p07-mpmath/requirements.lock\n")


def prepare(profile, transplant_receipt, output, *, downloader=fetch):
    if output.exists():
        raise ValueError("fresh SymPy runtime context required")
    raw_profile = profile.read_bytes().replace(b"\r\n", b"\n")
    if raw_profile != PROFILE.read_bytes().replace(b"\r\n", b"\n"):
        raise ValueError("profile differs from reviewed checkout")
    config = json.loads(raw_profile)
    raw_parent, parent = parent_report(transplant_receipt)
    if (config["task"]["official_base_image"] != transplant.BASE or config["task"]["base_commit"] != transplant.COMMIT
            or config["donor"]["image"] != transplant.DONOR
            or config["transplant"]["launcher_sha256"] != parent["launcher_sha256"]
            or config["transplant"]["source_git_lf_sha256"] != transplant.sha(Path(transplant.__file__).read_bytes().replace(b"\r\n", b"\n"))):
        raise ValueError("profile runtime differs from reviewed transplant")
    record = config["dependency"]
    metadata = json.loads(downloader(record["primary_metadata_url"]))
    published = [item for item in metadata["urls"] if item["filename"] == record["filename"]]
    if (len(published) != 1 or published[0]["url"] != record["url"]
            or published[0]["digests"]["sha256"] != record["sha256"]):
        raise ValueError("primary PyPI publication differs")
    raw = downloader(record["url"])
    module_hash = verified_wheel(record, raw)
    output.mkdir(parents=True)
    (output / record["filename"]).write_bytes(raw)
    requirements = f"mpmath==1.2.1 --hash=sha256:{record['sha256']}\n"
    (output / "requirements.lock").write_bytes(requirements.encode())
    (output / "Dockerfile").write_bytes(dockerfile(parent["image_id"]).encode())
    (output / "parent-transplant.json").write_bytes(raw_parent)
    wheel = {key: record[key] for key in ("name", "version", "filename", "url", "sha256", "wheel_metadata_sha256")}
    wheel["module_member_sha256"] = module_hash
    report = {"status": "prepared_not_built", "task": "sympy__sympy-13971", "base_image": transplant.BASE,
              "donor_image": transplant.DONOR, "base_commit": transplant.COMMIT,
              "profile_sha256": transplant.sha(raw_profile), "parent_image_id": parent["image_id"],
              "parent_transplant_report_sha256": transplant.sha(raw_parent), "wheel": wheel,
              "requirements_sha256": transplant.sha(requirements.encode()),
              "dockerfile_sha256": transplant.sha(dockerfile(parent["image_id"]).encode()),
              "launcher_sha256": parent["launcher_sha256"], "model_calls": 0,
              "target_setup_or_tests_executed": False, "calibrated": False}
    transplant.save(output / "preparation.json", report)
    return report


def build(context, output):
    if platform.system() != "Linux" or output.exists():
        raise ValueError("approved Linux Docker worker and fresh output required")
    prepared = json.loads((context / "preparation.json").read_text())
    raw_parent = (context / "parent-transplant.json").read_bytes()
    parent = json.loads(raw_parent)
    profile_raw = PROFILE.read_bytes().replace(b"\r\n", b"\n")
    dependency = json.loads(profile_raw)["dependency"]
    expected_requirements = f"mpmath==1.2.1 --hash=sha256:{dependency['sha256']}\n".encode()
    expected_wheel = {key: dependency[key] for key in ("name", "version", "filename", "url", "sha256", "wheel_metadata_sha256")}
    if (prepared.get("profile_sha256") != transplant.sha(profile_raw)
            or any(prepared["wheel"].get(key) != value for key, value in expected_wheel.items())
            or prepared.get("base_image") != transplant.BASE or prepared.get("donor_image") != transplant.DONOR
            or prepared.get("base_commit") != transplant.COMMIT
            or (context / "requirements.lock").read_bytes() != expected_requirements
            or transplant.sha(raw_parent) != prepared["parent_transplant_report_sha256"]
            or parent.get("status") != "transplant_stdlib_qualified" or not parent.get("cleanup_confirmed")
            or parent["image_id"] != prepared["parent_image_id"]
            or (context / "Dockerfile").read_bytes() != dockerfile(parent["image_id"]).encode()
            or transplant.sha((context / "requirements.lock").read_bytes()) != prepared["requirements_sha256"]
            or verified_wheel(prepared["wheel"], (context / prepared["wheel"]["filename"]).read_bytes()) != prepared["wheel"]["module_member_sha256"]):
        raise ValueError("prepared runtime context changed")
    output.mkdir(parents=True)
    report = {**prepared, "status": "runtime_unavailable", "parent_transplant_report": parent,
              "provider_credentials": "none"}
    names = []

    def execute(args, timeout=60):
        env = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}}
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False, env=env)

    def checked(args, timeout=60):
        process = execute(args, timeout)
        if process.returncode:
            raise RuntimeError("runtime provisioning command failed: " + args[1])
        return process.stdout.decode().strip()

    def probe(image_id):
        name = "p07-sympy-runtime-" + uuid.uuid4().hex[:24]
        names.append(name)
        checked(["docker", "create", "--name", name, "--network", "none", "--read-only", "--user", "65534:65534",
                 "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "1g", "--cpus", "2",
                 "--pids-limit", "128", "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m,mode=1777",
                 "--workdir", "/tmp", "--entrypoint", "/bin/sleep", image_id, "infinity"])
        config = json.loads(checked(["docker", "inspect", name]))[0]
        host = config["HostConfig"]
        if (config.get("Image") != image_id or config.get("Mounts") or host.get("Binds") or host.get("Privileged")
                or host.get("NetworkMode") != "none" or not host.get("ReadonlyRootfs") or host.get("CapDrop") != ["ALL"]
                or "no-new-privileges" not in host.get("SecurityOpt", []) or host.get("Memory") != 1024**3
                or host.get("NanoCpus") != 2_000_000_000 or host.get("PidsLimit") != 128
                or config["Config"].get("User") != "65534:65534"):
            raise ValueError("runtime provisioning isolation mismatch")
        checked(["docker", "start", name])
        git = ["docker", "exec", name, "git", "-c", "safe.directory=/testbed", "-C", "/testbed"]
        source = {"head": checked([*git, "rev-parse", "HEAD"]), "diff": checked([*git, "diff", "--binary", "HEAD"]),
                  "status": checked([*git, "status", "--porcelain", "--untracked-files=all"])}
        return name, source

    try:
        actual_parent = json.loads(checked(["docker", "image", "inspect", parent["image_id"]]))[0]
        if actual_parent["Id"] != parent["image_id"] or actual_parent["RootFS"] != parent["image_inspect"]["RootFS"]:
            raise ValueError("local qualified parent identity mismatch")
        name, source = probe(parent["image_id"])
        report["before"] = source
        if source != parent["after"]:
            raise ValueError("parent target source differs from qualification")
        checked(["docker", "tag", parent["image_id"], parent_tag(parent["image_id"])])
        tagged = json.loads(checked(["docker", "image", "inspect", parent_tag(parent["image_id"])]))[0]
        if tagged["Id"] != parent["image_id"]:
            raise ValueError("local build parent tag identity mismatch")
        tag = "p07-sympy13971:" + uuid.uuid4().hex
        built = execute(["docker", "build", "--network", "none", "--pull=false", "--tag", tag,
                         "--label", "atomic.p07.sympy.base=" + transplant.BASE,
                         "--label", "atomic.p07.sympy.parent=" + parent["image_id"],
                         "--label", "atomic.p07.sympy.profile=" + prepared["profile_sha256"], str(context)], 900)
        (output / "build.stdout").write_bytes(built.stdout)
        (output / "build.stderr").write_bytes(built.stderr)
        if built.returncode:
            raise RuntimeError("offline mpmath wheel installation failed")
        image = json.loads(checked(["docker", "image", "inspect", tag]))[0]
        layers = actual_parent["RootFS"]["Layers"]
        if (not re.fullmatch(r"sha256:[0-9a-f]{64}", image["Id"]) or not layers
                or image["RootFS"]["Layers"][:len(layers)] != layers or len(image["RootFS"]["Layers"]) <= len(layers)):
            raise ValueError("mpmath overlay image ancestry mismatch")
        report.update(image_id=image["Id"], image_inspect=image)
        name, report["after"] = probe(image["Id"])
        if report["before"] != report["after"]:
            raise ValueError("dependency provisioning changed target source")
        for label, code in (("runtime", transplant.PROBE), ("mpmath", MPMATH_PROBE)):
            process = execute(["docker", "exec", name, transplant.LAUNCHER, "-c", code], 45)
            (output / (label + ".stdout")).write_bytes(process.stdout)
            (output / (label + ".stderr")).write_bytes(process.stderr)
            report[label + "_output"] = {"exit_code": process.returncode, "stdout_sha256": transplant.sha(process.stdout),
                                          "stderr_sha256": transplant.sha(process.stderr)}
            if process.returncode:
                raise RuntimeError(label + " private-runtime probe failed")
            report[label + "_probe"] = json.loads(process.stdout)
        observed = report["mpmath_probe"]
        if (not transplant.validate_probe(report["runtime_probe"]) or observed.get("version") != "1.2.1"
                or observed.get("module_path") != transplant.PREFIX + "/lib/python3.6/site-packages/mpmath/__init__.py"
                or observed.get("module_sha256") != prepared["wheel"]["module_member_sha256"]
                or observed.get("prefix") != transplant.PREFIX
                or observed.get("executable") != transplant.PREFIX + "/bin/python3.6"):
            raise ValueError("private runtime or installed mpmath identity mismatch")
        report["status"] = "runtime_dependencies_qualified"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    finally:
        report["cleanup"] = []
        for name in names:
            try:
                process = execute(["docker", "rm", "-f", name], 30)
                absent = execute(["docker", "container", "ls", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"], 15)
                report["cleanup"].append({"container": name, "exit": process.returncode,
                    "confirmed": absent.returncode == 0 and not absent.stdout.strip()})
            except (OSError, subprocess.SubprocessError) as exc:
                report["cleanup"].append({"container": name, "confirmed": False, "error_type": type(exc).__name__})
        report["cleanup_confirmed"] = all(item["confirmed"] for item in report["cleanup"])
        report["cleanup_exit"] = 0 if report["cleanup_confirmed"] else None
        if not report["cleanup_confirmed"]:
            report["status"] = "cleanup_uncertain"
        transplant.save(output / "sympy-runtime.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    preparing = sub.add_parser("prepare")
    preparing.add_argument("--profile", type=Path, required=True)
    preparing.add_argument("--transplant-receipt", type=Path, required=True)
    preparing.add_argument("--output", type=Path, required=True)
    building = sub.add_parser("build")
    building.add_argument("--context", type=Path, required=True)
    building.add_argument("--output", type=Path, required=True)
    args = vars(parser.parse_args())
    action = args.pop("action")
    result = prepare(**args) if action == "prepare" else build(**args)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] in {"prepared_not_built", "runtime_dependencies_qualified"} else 1)
