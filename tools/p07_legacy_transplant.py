"""Copy a pinned legacy runtime into a private prefix; no target imports/tests."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import uuid
from pathlib import Path

DONOR = "library/python@sha256:d59ee182c4629dd33b240b9bac1a6ff44276e79e66117eba17aed4016845d3b2"
BASE = "swebench/sweb.eval.x86_64.sympy_1776_sympy-13971@sha256:cc0369f168735cf7b9add74f5d3a50cf3c51a03549b798fa853181633ed2921a"
COMMIT = "84c125972ad535b2dfb245f8d311d347b45e5b8a"
PREFIX = "/opt/p07-python36"
LAUNCHER = "/opt/p07-runtime/python36"
LAUNCHER_SOURCE = '''#!/bin/sh
export PYTHONHOME=/opt/p07-python36
exec /opt/p07-donor/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 --library-path /opt/p07-python36/lib:/opt/p07-donor/lib/x86_64-linux-gnu:/opt/p07-donor/usr/lib/x86_64-linux-gnu /opt/p07-python36/bin/python3.6 -I -B "$@"
'''
PROBE = r'''
import importlib,json,os,sys,sysconfig
result={"version_info":list(sys.version_info[:3]),"version":sys.version,"executable":sys.executable,
        "prefix":sys.prefix,"base_prefix":sys.base_prefix,"stdlib":sysconfig.get_path('stdlib'),
        "soabi":sysconfig.get_config_var('SOABI'),"sys_path":sys.path,"imports":{}}
for name in ('ssl','ctypes','sqlite3','bz2','lzma','encodings'):
    try:
        module=importlib.import_module(name)
        result['imports'][name]={"status":"imported","path":getattr(module,'__file__',None)}
    except Exception as exc:
        result['imports'][name]={"status":"exception","exception_type":type(exc).__name__}
try:
    with open('/proc/self/maps') as stream:
        result['mapped_libraries']=sorted(set(line.split()[-1] for line in stream if '/' in line))
except Exception as exc:
    result['maps_exception_type']=type(exc).__name__
print(json.dumps(result,sort_keys=True))
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.write_bytes((json.dumps(value, sort_keys=True, indent=2) + "\n").encode())


def dockerfile():
    return (f"FROM {DONOR} AS donor\nFROM {BASE}\nUSER root\n"
            "COPY --from=donor /usr/local/ /opt/p07-python36/\n"
            "COPY --from=donor /lib/x86_64-linux-gnu/ /opt/p07-donor/lib/x86_64-linux-gnu/\n"
            "COPY --from=donor /usr/lib/x86_64-linux-gnu/ /opt/p07-donor/usr/lib/x86_64-linux-gnu/\n"
            "COPY python36 /opt/p07-runtime/python36\n"
            "RUN --network=none chmod 755 /opt/p07-runtime/python36\n")


def prepare(output):
    if output.exists():
        raise ValueError("fresh transplant preparation required")
    output.mkdir(parents=True)
    (output / "Dockerfile").write_bytes(dockerfile().encode())
    (output / "python36").write_bytes(LAUNCHER_SOURCE.encode())
    receipt = {"status": "prepared_not_built", "base_image": BASE, "donor_image": DONOR,
               "base_commit": COMMIT, "dockerfile_sha256": sha(dockerfile().encode()),
               "launcher_sha256": sha(LAUNCHER_SOURCE.encode()), "probe_sha256": sha(PROBE.encode()),
               "model_calls": 0, "target_setup_or_tests_executed": False, "calibrated": False}
    save(output / "preparation.json", receipt)
    return receipt


def validate_probe(result):
    names = {"ssl", "ctypes", "sqlite3", "bz2", "lzma", "encodings"}
    imports = result.get("imports", {})
    maps = result.get("mapped_libraries", [])
    return (result.get("version_info") == [3, 6, 15] and result.get("prefix") == PREFIX
            and result.get("base_prefix") == PREFIX and result.get("stdlib") == PREFIX + "/lib/python3.6"
            and result.get("executable") == PREFIX + "/bin/python3.6"
            and set(imports) == names
            and all(imports[name].get("status") == "imported"
                    and isinstance(imports[name].get("path"), str)
                    and imports[name]["path"].startswith(PREFIX + "/lib/python3.6/") for name in names)
            and bool(maps) and any("/libpython3.6m.so" in path and path.startswith(PREFIX + "/lib/") for path in maps)
            and any("/libc-" in path and path.startswith("/opt/p07-donor/lib/") for path in maps)
            and all(path.startswith((PREFIX + "/", "/opt/p07-donor/")) for path in maps)
            and all(not path or path.startswith(PREFIX + "/") for path in result.get("sys_path", [])))


def build(context, output):
    if platform.system() != "Linux" or output.exists():
        raise ValueError("approved Linux Docker worker and fresh output required")
    if ((context / "Dockerfile").read_bytes() != dockerfile().encode()
            or (context / "python36").read_bytes() != LAUNCHER_SOURCE.encode()):
        raise ValueError("transplant context differs from fixed reviewed bytes")
    output.mkdir(parents=True)
    report = {"status": "transplant_unavailable", "base_image": BASE, "donor_image": DONOR,
              "base_commit": COMMIT, "prefix": PREFIX, "launcher": LAUNCHER,
              "model_calls": 0, "provider_credentials": "none", "target_setup_or_tests_executed": False,
              "calibrated": False, "probe_sha256": sha(PROBE.encode()),
              "dockerfile_sha256": sha(dockerfile().encode()), "launcher_sha256": sha(LAUNCHER_SOURCE.encode())}
    containers = []

    def execute(args, timeout=60):
        clean = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}}
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False, env=clean)

    def checked(args, timeout=60):
        process = execute(args, timeout)
        if process.returncode:
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
            "--label", "atomic.p07.legacy.launcher=" + sha(LAUNCHER_SOURCE.encode()), str(context)], 900)
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
        process = execute(["docker", "exec", after, LAUNCHER, "-c", PROBE], 45)
        (output / "probe.stdout").write_bytes(process.stdout)
        (output / "probe.stderr").write_bytes(process.stderr)
        report.update(process_exit=process.returncode, stdout_sha256=sha(process.stdout), stderr_sha256=sha(process.stderr))
        if process.returncode:
            raise RuntimeError("private-prefix runtime startup failed; original logs retained")
        report["observed"] = json.loads(process.stdout)
        compatible = validate_probe(report["observed"])
        report.update(status="transplant_stdlib_qualified" if compatible else "transplant_probe_incompatibility",
                      probe_compatible=compatible)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
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
        save(output / "legacy-transplant.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    preparing = sub.add_parser("prepare")
    preparing.add_argument("--output", type=Path, required=True)
    building = sub.add_parser("build")
    building.add_argument("--context", type=Path, required=True)
    building.add_argument("--output", type=Path, required=True)
    args = vars(parser.parse_args())
    result = prepare(**{key: value for key, value in args.items() if key != "action"}) if args["action"] == "prepare" else build(**{key: value for key, value in args.items() if key != "action"})
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] in {"prepared_not_built", "transplant_stdlib_qualified"} else 1)
