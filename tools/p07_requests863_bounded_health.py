"""Source-free Requests863 service health in the ordinary worker limits.

Runs the previously reviewed real health program on an independently admitted
image. This does not run the target suite or grant TaskRunner admission.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import p07_python27_support as legacy
import p07_requests863_python27_health as health
import p07_requests863_python27_image as provision
import p07_requests863_python27_support as transplant

ENGINE = "/usr/bin/docker"
TASK = "psf__requests-863"


def sha(value):
    return hashlib.sha256(value).hexdigest()


def run(receipt: Path, output: Path):
    if os.geteuid() != 0 or output.exists():
        raise ValueError("root Linux and fresh output required")
    output.mkdir(parents=True)
    raw, admitted = provision.admit_receipt(receipt)
    if admitted["task"] != TASK:
        raise ValueError("exact 863 image required")
    commit = subprocess.check_output(["git", "-c", "safe.directory=" + str(Path.cwd()), "rev-parse", "HEAD"], text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("exact source commit required")
    environment = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
    report = {"status": "not_qualified", "tested_commit": commit, "task_id": TASK,
              "scope": "source-free service health under ordinary worker controls", "provider_calls": 0,
              "target_tests": False, "model_quality": "not_measured", "image_id": admitted["image_id"],
              "provisioning_receipt_sha256": sha(raw)}
    cid = None
    with tempfile.TemporaryDirectory(prefix="p07-863-health-") as temporary:
        cidfile = Path(temporary) / "container.cid"

        def command(argv, *, input_bytes=None, timeout=45):
            return subprocess.run(argv, input=input_bytes, capture_output=True, env=environment,
                                  timeout=timeout, check=False)

        try:
            hosts = health.TASK_HOSTS[TASK]
            args = [ENGINE, "create", "--cidfile", str(cidfile), "--init", "--pull=never", "--network=none",
                    "--read-only", "--user=65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                    "--memory=1g", "--cpus=2", "--pids-limit=128", "--tmpfs=/tmp:rw,noexec,nosuid,size=134217728,mode=1777",
                    "--sysctl=net.ipv4.ip_unprivileged_port_start=0",
                    *["--add-host=" + host + ":" + address for host, address in sorted(hosts.items())],
                    "--entrypoint=/bin/sleep", admitted["image_id"], "infinity"]
            created = command(args)
            if created.returncode:
                raise ValueError("container create failed: " + created.stderr.decode(errors="replace")[:500])
            cid = cidfile.read_text(encoding="ascii").strip()
            if not re.fullmatch(r"[0-9a-f]{64}", cid):
                raise ValueError("immutable container ID missing")
            if command([ENGINE, "start", cid]).returncode:
                raise ValueError("container start failed")

            def inspect():
                process = command([ENGINE, "inspect", cid])
                if process.returncode:
                    raise ValueError("container inspect failed")
                config = json.loads(process.stdout)[0]
                host, entry, state = config["HostConfig"], config["Config"], config["State"]
                if (config["Id"] != cid or config["Image"] != admitted["image_id"] or not state["Running"]
                    or entry["User"] != "65534:65534" or host["Init"] is not True or host["Privileged"]
                    or not host["ReadonlyRootfs"] or host["NetworkMode"] != "none" or host["CapDrop"] != ["ALL"]
                    or host.get("CapAdd") or "no-new-privileges" not in host.get("SecurityOpt", [])
                    or host["Memory"] != 1024**3 or host["NanoCpus"] != 2_000_000_000 or host["PidsLimit"] != 128
                    or sorted(host.get("ExtraHosts") or []) != sorted(k + ":" + v for k, v in hosts.items())
                    or host.get("Sysctls") != {"net.ipv4.ip_unprivileged_port_start": "0"}
                    or any(mount.get("Type") != "tmpfs" or mount.get("Destination") != "/tmp"
                           for mount in config.get("Mounts", []))
                    or host.get("Tmpfs") != {"/tmp": "rw,noexec,nosuid,size=134217728,mode=1777"}
                    or host.get("Binds") or host.get("VolumesFrom")):
                    raise ValueError("actual worker isolation differs")
                inode = Path("/proc/" + str(state["Pid"]) + "/ns/net").stat().st_ino
                if inode == Path("/proc/self/ns/net").stat().st_ino:
                    raise ValueError("worker shares host network namespace")
                return {"pid": state["Pid"], "network_namespace": inode}

            before = inspect()
            routes = command(["/usr/bin/nsenter", "-t", str(before["pid"]), "-n", "ip", "-j", "route", "show"])
            if routes.returncode or any(route.get("dst") == "default" for route in json.loads(routes.stdout)):
                raise ValueError("worker route inventory unavailable or contains a default route")
            external = command(["/usr/bin/nsenter", "-t", str(before["pid"]), "-n", "ip", "route", "get", "1.1.1.1"])
            if external.returncode == 0:
                raise ValueError("external route unexpectedly available")
            report["external_route_exit"] = external.returncode
            code = health.bootstrap_source(TASK, admitted["installed_files"], admitted["certificate_files"],
                                           (receipt / "wrongca.pem").read_text(encoding="ascii"))
            result = command([ENGINE, "exec", "-i", "--user=65534:65534", "-w", "/tmp", cid,
                              transplant.LAUNCHER, "-c", legacy.STDIN_BOOTSTRAP], input_bytes=code.encode(), timeout=120)
            (output / "health.stdout").write_bytes(result.stdout)
            (output / "health.stderr").write_bytes(result.stderr)
            report.update(exit_code=result.returncode, stdout_sha256=sha(result.stdout), stderr_sha256=sha(result.stderr),
                          bootstrap_sha256=sha(code.encode()), worker_before=before, worker_after=inspect())
            if result.returncode or len(result.stdout) + len(result.stderr) > 1_000_000 or before != report["worker_after"]:
                raise ValueError("bounded same-worker service health failed")
            report["health"] = health.validate_health(result.stdout.decode(), TASK, admitted["installed_files"],
                                                       admitted["certificate_files"])
            report["status"] = "bounded_service_health_qualified"
        except Exception as error:
            report.update(error_type=type(error).__name__, error=str(error)[:500])
        finally:
            if cid is None and cidfile.is_file():
                cid = cidfile.read_text(encoding="ascii").strip()
            if cid and re.fullmatch(r"[0-9a-f]{64}", cid):
                removed = command([ENGINE, "rm", "-f", cid])
                absent = command([ENGINE, "container", "ls", "-a", "--no-trunc", "--filter", "id=" + cid, "--format", "{{.ID}}"])
                report["cleanup"] = {"container_id": cid, "remove_exit": removed.returncode,
                                     "confirmed": absent.returncode == 0 and not absent.stdout.strip()}
            else:
                report["cleanup"] = {"container_id": cid, "confirmed": False}
            if not report["cleanup"]["confirmed"]:
                report["status"] = "cleanup_uncertain"
    (output / "summary.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    raise SystemExit(0 if run(options.runtime_receipt, options.output)["status"] == "bounded_service_health_qualified" else 1)
