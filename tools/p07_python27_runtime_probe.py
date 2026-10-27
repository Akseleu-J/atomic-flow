"""Qualify an immutable legacy Python donor; never run target source or setup."""
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

IMAGE = "library/python@sha256:c934af72b8bd03b9804d5bde2569c320926e70392d708d113a2e71bcf98c8a20"
DIGEST = IMAGE.split("@", 1)[1]
SSLV3_IMAGE = "library/python@sha256:f644f2f797711ee82575b6644eba0f914c2bcc8137dafdb1b39cc6fa3b70cfa2"
PROBE = r'''
import collections, importlib, json, sys, sysconfig
result={"version":sys.version,"version_info":list(sys.version_info[:3]),"executable":sys.executable,"prefix":sys.prefix,"unicode_size":sysconfig.get_config_var('Py_UNICODE_SIZE'),"ldlibrary":sysconfig.get_config_var('LDLIBRARY'),"libdir":sysconfig.get_config_var('LIBDIR'),"probes":{}}
for name in ('ssl','ctypes','sqlite3','bz2','zlib'):
    try:
        module=importlib.import_module(name)
        item={'status':'imported','path':getattr(module,'__file__',None)}
        if name=='ssl':item['openssl_version']=module.OPENSSL_VERSION
        result['probes'][name]=item
    except Exception as exc:
        result['probes'][name]={'status':'exception','exception_type':type(exc).__name__}
class SyntheticRequestException(IOError):
    pass
class SyntheticConnectionError(SyntheticRequestException):
    pass
exc=SyntheticConnectionError('p07-message-contract')
result['probes']['exception_message']={'status':'resolved','value':exc.message,'matches':exc.message=='p07-message-contract'}
result['probes']['collections_mapping']={'status':'resolved','module':collections.Mapping.__module__}
print(json.dumps(result,sort_keys=True))
'''


def profile_inputs(profile):
    if profile == "python2718":
        return IMAGE, PROBE, [2, 7, 18]
    if profile == "python2713-sslv3":
        probe = PROBE.replace(
            "if name=='ssl':item['openssl_version']=module.OPENSSL_VERSION",
            "if name=='ssl':\n            item['openssl_version']=module.OPENSSL_VERSION\n"
            "            context=module.SSLContext(module.PROTOCOL_SSLv3)\n"
            "            item['sslv3_context_constructed']=context.protocol==module.PROTOCOL_SSLv3",
        )
        return SSLV3_IMAGE, probe, [2, 7, 13]
    raise ValueError("fixed historical donor profile required")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.write_bytes((json.dumps(value, sort_keys=True, indent=2) + "\n").encode())


def runtime_command(name, image_id, *, profile="python2718"):
    if not re.fullmatch(r"p07-legacy-[a-f0-9]{24}", name) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("owned container name and immutable image ID required")
    _, probe, _ = profile_inputs(profile)
    return ["docker", "create", "--name", name, "--network", "none", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--read-only", "--user", "65534:65534",
            "--memory", "1g", "--cpus", "2", "--pids-limit", "128",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m,mode=1777", "--workdir", "/tmp",
            "--env", "HOME=/tmp", "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--entrypoint", "/usr/local/bin/python2.7", image_id, "-E", "-s", "-S", "-B", "-c", probe]


def validate_container(config, image_id):
    host = config["HostConfig"]
    if (config.get("Image") != image_id or config.get("Mounts") or host.get("Binds")
            or host.get("NetworkMode") != "none" or host.get("Privileged")
            or host.get("CapDrop") != ["ALL"] or "no-new-privileges" not in host.get("SecurityOpt", [])
            or not host.get("ReadonlyRootfs") or host.get("Memory") != 1024 ** 3
            or host.get("NanoCpus") != 2_000_000_000 or host.get("PidsLimit") != 128
            or host.get("Tmpfs") != {"/tmp": "rw,noexec,nosuid,size=128m,mode=1777"}
            or config.get("Config", {}).get("User") != "65534:65534"):
        raise ValueError("legacy donor container isolation mismatch")


def validate_observed(observed, *, profile="python2718"):
    _, _, version = profile_inputs(profile)
    probes = observed.get("probes", {})
    expected = {"ssl", "ctypes", "sqlite3", "bz2", "zlib", "exception_message", "collections_mapping"}
    return (observed.get("version_info") == version
            and (profile != "python2713-sslv3" or probes.get("ssl", {}).get("sslv3_context_constructed") is True)
            and observed.get("executable") == "/usr/local/bin/python2.7"
            and observed.get("prefix") == "/usr/local" and observed.get("unicode_size") == 4
            and set(probes) == expected
            and all(probes[name].get("status") == "imported" for name in expected - {"exception_message", "collections_mapping"})
            and probes["exception_message"].get("status") == "resolved"
            and probes["exception_message"].get("matches") is True
            and probes["exception_message"].get("value") == "p07-message-contract"
            and probes["collections_mapping"].get("status") == "resolved")


def qualify(output, *, profile="python2718"):
    if platform.system() != "Linux" or output.exists():
        raise ValueError("approved Linux Docker worker and fresh output required")
    selected_image, probe, _ = profile_inputs(profile)
    selected_digest = selected_image.split("@", 1)[1]
    output.mkdir(parents=True)
    report = {"status": "probe_unavailable", "profile": profile, "image": selected_image, "probe_source_sha256": sha(probe.encode()),
              "model_calls": 0, "target_source_present": False, "target_setup_or_tests_executed": False,
              "provider_credentials": "none", "legacy_transplant": "not_attempted", "calibrated": False}
    name = "p07-legacy-" + uuid.uuid4().hex[:24]
    intent = False

    def execute(args, *, timeout=60):
        allowed = {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}
        clean = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False, env=clean)

    try:
        pulled = execute(["docker", "pull", selected_image], timeout=600)
        (output / "pull.stdout").write_bytes(pulled.stdout)
        (output / "pull.stderr").write_bytes(pulled.stderr)
        if pulled.returncode:
            raise RuntimeError("immutable donor pull failed")
        inspected = execute(["docker", "image", "inspect", selected_image])
        if inspected.returncode:
            raise RuntimeError("donor image identity unavailable")
        images = json.loads(inspected.stdout)
        if not isinstance(images, list) or len(images) != 1:
            raise ValueError("ambiguous donor image identity")
        image = images[0]
        image_id = image.get("Id", "")
        digests = image.get("RepoDigests", [])
        admitted = {"library/python@" + selected_digest, "python@" + selected_digest, "docker.io/library/python@" + selected_digest}
        if (not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) or not set(digests) & admitted
                or image.get("Architecture") != "amd64" or image.get("Os") != "linux"):
            raise ValueError("donor digest or platform mismatch")
        report.update(image_id=image_id, image_inspect=image)
        intent = True  # An uncertain create also requires daemon-confirmed absence.
        created = execute(runtime_command(name, image_id, profile=profile))
        if created.returncode:
            raise RuntimeError("donor container creation failed")
        inspected = execute(["docker", "inspect", name])
        if inspected.returncode:
            raise RuntimeError("donor container identity unavailable")
        container = json.loads(inspected.stdout)[0]
        validate_container(container, image_id)
        report["container_inspect"] = container
        # No run/exec command can accept target files or credentials. Docker only
        # receives the reviewed fixed code embedded above and bounded env literals.
        started = execute(["docker", "start", name])
        if started.returncode:
            raise RuntimeError("donor process did not start")
        waiting = execute(["docker", "wait", name], timeout=45)
        if waiting.returncode or not re.fullmatch(rb"[0-9]+\s*", waiting.stdout):
            raise RuntimeError("donor process terminal status unavailable")
        report["process_exit"] = int(waiting.stdout)
        logs = execute(["docker", "logs", name])
        (output / "probe.stdout").write_bytes(logs.stdout)
        (output / "probe.stderr").write_bytes(logs.stderr)
        report.update(stdout_sha256=sha(logs.stdout), stderr_sha256=sha(logs.stderr))
        if logs.returncode or report["process_exit"] != 0:
            raise RuntimeError("donor process failed; original logs retained")
        observed = json.loads(logs.stdout)
        report["observed"] = observed
        compatible = validate_observed(observed, profile=profile)
        report.update(status="donor_probes_compatible" if compatible else "donor_probe_incompatibility",
                      probe_compatible=compatible)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    finally:
        if intent:
            try:
                cleanup = execute(["docker", "rm", "-f", name], timeout=30)
                report["cleanup_exit"] = cleanup.returncode
                # Daemon-confirmed absence is required even when rm claims failure.
                absent = execute(["docker", "container", "ls", "-a", "--filter", "name=^/" + name + "$",
                                  "--format", "{{.ID}}"], timeout=15)
                confirmed = absent.returncode == 0 and not absent.stdout.strip()
                report["cleanup_confirmed"] = confirmed
                if not confirmed:
                    report.update(status="cleanup_uncertain", probe_compatible=False)
            except (OSError, subprocess.SubprocessError) as exc:
                report.update(status="cleanup_uncertain", cleanup_confirmed=False, probe_compatible=False,
                              cleanup_error_type=type(exc).__name__)
        save(output / "python27-runtime-probe.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=("python2718", "python2713-sslv3"), default="python2718")
    args = parser.parse_args()
    result = qualify(args.output, profile=args.profile)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "donor_probes_compatible" else 1)
