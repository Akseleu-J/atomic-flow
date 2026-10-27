"""Synthetic host-owned loopback timeout topology qualification, no target imports."""
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

IMAGE = "swebench/sweb.eval.x86_64.psf_1776_requests-3362@sha256:c5dc4ba64a2d97d6cff6b82fe9c0561589df13f23df6883d05e99b630942bbd1"
PYTHON = "/opt/miniconda3/envs/testbed/bin/python"
DESTINATION = "10.255.255.1"
RULE = ["-d", DESTINATION + "/32", "-p", "tcp", "--dport", "80", "-j", "DROP"]
PROBE = r'''
import json,socket,threading,time,sys
server=socket.socket()
server.bind(('127.0.0.1',0))
server.listen(1)
def serve():
    connection,_=server.accept()
    with connection:
        connection.sendall(b'p07-real-loopback')
thread=threading.Thread(target=serve,daemon=True)
thread.start()
with socket.create_connection(server.getsockname(),timeout=1) as connection:
    received=connection.recv(64)
thread.join(2)
server.close()
if received!=b'p07-real-loopback' or thread.is_alive():
    raise RuntimeError('real loopback health failed')
started=time.monotonic()
try:
    connection=socket.create_connection(('10.255.255.1',80),timeout=0.1)
except socket.timeout:
    elapsed=time.monotonic()-started
else:
    connection.close()
    raise RuntimeError('blackhole unexpectedly connected')
if not 0.08<=elapsed<=2:
    raise RuntimeError('timeout timing outside bounded synthetic window')
print(json.dumps({'loopback':'real_socket_pass','blackhole':'socket.timeout',
    'destination':['10.255.255.1',80],'requested_timeout':0.1,'elapsed':elapsed,
    'isolated':sys.flags.isolated,'executable':sys.executable},sort_keys=True))
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def execute(args, timeout=30):
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}}
    return subprocess.run(args, capture_output=True, timeout=timeout, check=False, env=environment)


def checked(args, timeout=30):
    result = execute(args, timeout)
    if result.returncode:
        raise RuntimeError("fixed worker command failed: " + args[0])
    return result.stdout


def process_identity(pid):
    if type(pid) is not int or pid <= 1:
        raise ValueError("invalid owned container PID")
    raw = Path(f"/proc/{pid}/stat").read_text()
    fields = raw[raw.rfind(")") + 2:].split()
    return {"pid": pid, "start_ticks": fields[19],
            "netns_inode": Path(f"/proc/{pid}/ns/net").stat().st_ino}


def namespace_command(namespace, program, *args):
    if not re.fullmatch(r"/proc/[1-9][0-9]*/fd/[0-9]+", namespace) or program not in {"ip", "iptables"}:
        raise ValueError("fixed host network operation required")
    return ["/usr/bin/nsenter", "--net=" + namespace, "--",
            "/usr/sbin/" + program, *args]


def setup_commands(namespace):
    return [namespace_command(namespace, "ip", "route", "add", "10.255.255.1/32", "dev", "lo", "src", "127.0.0.1"),
            namespace_command(namespace, "iptables", "-w", "5", "-A", "OUTPUT", *RULE)]


def inspect_policy(config, image_id):
    host = config["HostConfig"]
    if (config.get("Image") != image_id or config.get("Mounts") or host.get("Binds")
            or host.get("Privileged") or host.get("NetworkMode") != "none"
            or host.get("CapDrop") != ["ALL"] or host.get("CapAdd")
            or "no-new-privileges" not in host.get("SecurityOpt", [])
            or not host.get("ReadonlyRootfs") or config["Config"].get("User") != "65534:65534"
            or host.get("Memory") != 256 * 1024**2 or host.get("NanoCpus") != 1_000_000_000
            or host.get("PidsLimit") != 64):
        raise ValueError("synthetic worker isolation mismatch")


def qualify(output):
    if platform.system() != "Linux" or os.geteuid() != 0 or output.exists():
        raise ValueError("fresh output on an approved root-owned Linux Docker host required")
    output.mkdir(parents=True)
    name = "p07-timeout-topology-" + uuid.uuid4().hex[:20]
    report = {"status": "unavailable", "model_calls": 0, "target_imports_or_tests": False,
              "calibrated": False, "verified_success": False, "base_image": IMAGE,
              "container_name": name, "probe_sha256": sha(PROBE.encode()),
              "profile": "network-none plus host-owned loopback route and TCP80 silent DROP"}
    namespace_fd = None
    try:
        checked(["docker", "pull", IMAGE], 600)
        image = json.loads(checked(["docker", "image", "inspect", IMAGE]))[0]
        if (not re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("Id", ""))
                or image.get("Os") != "linux" or image.get("Architecture") != "amd64"):
            raise ValueError("official image platform identity mismatch")
        report["image_id"] = image["Id"]
        checked(["docker", "create", "--name", name, "--network", "none", "--read-only", "--user", "65534:65534",
                 "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "256m", "--cpus", "1",
                 "--pids-limit", "64", "--tmpfs", "/tmp:rw,noexec,nosuid,size=8m,mode=1777",
                 "--workdir", "/tmp", "--entrypoint", "/bin/sleep", image["Id"], "infinity"])
        config = json.loads(checked(["docker", "inspect", name]))[0]
        inspect_policy(config, image["Id"])
        checked(["docker", "start", name])
        config = json.loads(checked(["docker", "inspect", name]))[0]
        inspect_policy(config, image["Id"])
        pid = config["State"]["Pid"]
        identity = process_identity(pid)
        host_ns = Path("/proc/self/ns/net").stat().st_ino
        if identity["netns_inode"] == host_ns:
            raise ValueError("worker shares host network namespace")
        namespace_fd = os.open(f"/proc/{pid}/ns/net", os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        namespace = f"/proc/{os.getpid()}/fd/{namespace_fd}"
        if os.fstat(namespace_fd).st_ino != identity["netns_inode"] or process_identity(pid) != identity:
            raise ValueError("namespace changed while pinning descriptor")
        report["namespace_identity"] = identity
        report["host_netns_inode"] = host_ns
        report["routes_before"] = json.loads(checked(namespace_command(namespace, "ip", "-j", "route", "show", "table", "all")))
        for command in setup_commands(namespace):
            if process_identity(pid) != identity:
                raise ValueError("owned namespace identity changed before setup")
            checked(command)
        report["routes_after"] = json.loads(checked(namespace_command(namespace, "ip", "-j", "route", "show", "table", "all")))
        if any(route.get("dst") == "default" for route in report["routes_after"]):
            raise ValueError("unexpected default route")
        if not any(route.get("dst") == "10.255.255.1" and route.get("dev") == "lo" for route in report["routes_after"]):
            raise ValueError("fixed loopback host route missing")
        external = execute(namespace_command(namespace, "ip", "route", "get", "1.1.1.1"))
        report["external_route_exit"] = external.returncode
        if external.returncode == 0:
            raise ValueError("unexpected external route")
        checked(namespace_command(namespace, "iptables", "-w", "5", "-C", "OUTPUT", *RULE))
        probe = checked(["docker", "exec", "-w", "/tmp", name, PYTHON, "-I", "-B", "-c", PROBE], 15)
        report["probe"] = json.loads(probe)
        if (report["probe"].get("loopback") != "real_socket_pass"
                or report["probe"].get("blackhole") != "socket.timeout"
                or report["probe"].get("isolated") != 1 or report["probe"].get("executable") != PYTHON):
            raise ValueError("synthetic interpreter/probe mismatch")
        counters = checked(namespace_command(namespace, "iptables", "-w", "5", "-L", "OUTPUT", "-v", "-n", "-x")).decode()
        report["firewall_counters"] = counters
        if not re.search(r"^\s*[1-9][0-9]*\s+[0-9]+\s+DROP\s+tcp\s+.*10\.255\.255\.1.*dpt:80", counters, re.MULTILINE):
            raise ValueError("no observed dropped TCP packet")
        if process_identity(pid) != identity or Path("/proc/self/ns/net").stat().st_ino != host_ns:
            raise ValueError("namespace identity changed")
        inspect_policy(json.loads(checked(["docker", "inspect", name]))[0], image["Id"])
        report["status"] = "synthetic_timeout_topology_qualified"
    except (OSError, ValueError, KeyError, IndexError, RuntimeError, subprocess.SubprocessError) as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    finally:
        try:
            removed = execute(["docker", "rm", "-f", name])
            absent = execute(["docker", "container", "ls", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"])
            report["cleanup_exit"] = removed.returncode
            report["cleanup_confirmed"] = absent.returncode == 0 and not absent.stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            report.update(cleanup_confirmed=False, cleanup_error_type=type(exc).__name__)
        if namespace_fd is not None:
            os.close(namespace_fd)
        if not report["cleanup_confirmed"]:
            report["status"] = "cleanup_uncertain"
        (output / "result.json").write_bytes((json.dumps(report, sort_keys=True, indent=2) + "\n").encode())
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    result = qualify(**vars(parser.parse_args()))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "synthetic_timeout_topology_qualified" else 1)
