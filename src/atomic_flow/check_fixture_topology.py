"""Fixed root-worker namespace setup; never import or execute target source.

This applies the accepted loopback timeout route and TCP80 DROP rule only.
The caller owns container creation, health checks, execution and cleanup. A
receipt is host observation, not fixture health, attestation or execution rights.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from atomic_flow.agents.execution import ExecutionUnavailable

DESTINATION = "10.255.255.1"
_RULE = ("-d", DESTINATION + "/32", "-p", "tcp", "--dport", "80", "-j", "DROP")
_MAX_OUTPUT = 64 * 1024


@dataclass(frozen=True, slots=True)
class OwnedTimeoutTopologyReceipt:
    container_id: str
    image_id: str
    fixture_digest: str
    nonce: str
    pid: int
    start_ticks: str
    netns_inode: int
    host_netns_inode: int
    route: tuple[str, ...]
    rule: tuple[str, ...]
    dropped_packets: int
    dropped_bytes: int
    status: str = "topology_applied_only"

    def as_dict(self) -> dict:
        return asdict(self)


def _process_identity(pid: int) -> tuple[int, str, int]:
    if type(pid) is not int or pid <= 1:
        raise ExecutionUnavailable("Invalid owned container PID")
    raw = Path("/proc/" + str(pid) + "/stat").read_text(encoding="ascii")
    end = raw.rfind(")")
    fields = raw[end + 2:].split()
    if end < 0 or len(fields) <= 19 or not re.fullmatch(r"[1-9][0-9]*", fields[19]):
        raise ExecutionUnavailable("Owned process birth identity unavailable")
    inode = Path("/proc/" + str(pid) + "/ns/net").stat().st_ino
    return pid, fields[19], inode


def _host_netns_inode() -> int:
    return Path("/proc/self/ns/net").stat().st_ino


def _command(argv: list[str], *, allow_failure=False) -> subprocess.CompletedProcess:
    # Fixed system PATH only: no inherited credentials, custom PATH or proxies.
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=False,
                            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"})
    if (type(result.returncode) is not int or type(result.stdout) is not bytes or type(result.stderr) is not bytes
            or len(result.stdout) > _MAX_OUTPUT or len(result.stderr) > _MAX_OUTPUT
            or result.returncode and not allow_failure):
        raise ExecutionUnavailable("Fixed namespace worker command failed or exceeded output bound")
    return result


def _inspect(engine: str, container_id: str, image_id: str) -> int:
    raw = _command([engine, "inspect", container_id]).stdout
    try:
        rows = json.loads(raw)
        if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
            raise ValueError
        config = rows[0]
        host, process, settings = config["HostConfig"], config["State"], config["Config"]
        user = settings.get("User")
        nonroot = type(user) is str and re.fullmatch(r"[1-9][0-9]*(?::[1-9][0-9]*)?", user) is not None
        if (config.get("Id") != container_id or config.get("Image") != image_id
                or process.get("Running") is not True or process.get("Paused") is not False
                or process.get("Restarting") is not False or not nonroot
                or host.get("Privileged") is not False or host.get("ReadonlyRootfs") is not True
                or host.get("NetworkMode") != "none" or host.get("CapDrop") != ["ALL"] or host.get("CapAdd")
                or "no-new-privileges" not in host.get("SecurityOpt", [])
                or any(type(host.get(key)) is not int or not 1 <= host[key] <= maximum
                       for key, maximum in (("Memory", 1024**3), ("NanoCpus", 2_000_000_000), ("PidsLimit", 128)))):
            raise ValueError
        pid = process["Pid"]
        if type(pid) is not int or pid <= 1:
            raise ValueError
        return pid
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise ExecutionUnavailable("Owned fixture container identity or isolation policy differs") from None


def _counters(raw: bytes) -> tuple[int, int]:
    matches = []
    for line in raw.decode("ascii").splitlines():
        fields = line.split()
        if (len(fields) == 11 and all(re.fullmatch(r"[0-9]+", field) for field in fields[:2])
                and fields[2] == "DROP" and fields[3] in {"tcp", "6"}
                and fields[4:8] == ["--", "*", "*", "0.0.0.0/0"]
                and fields[8] in {DESTINATION, DESTINATION + "/32"} and fields[9:] == ["tcp", "dpt:80"]):
            matches.append((int(fields[0]), int(fields[1])))
    if len(matches) != 1:
        raise ExecutionUnavailable("Exactly one fixed timeout firewall rule required")
    return matches[0]


def apply_owned_timeout_topology(*, engine: str, container_id: str, image_id: str,
                                 fixture_digest: str, nonce: str, cancel_check=None) -> OwnedTimeoutTopologyReceipt:
    """Apply only reviewed namespace commands to a pinned owned Linux netns.

    No sudo fallback, target probe, arbitrary command or service startup exists.
    A partial setup raises; the controller must discard its owned container.
    """
    if (platform.system() != "Linux" or not hasattr(os, "geteuid") or os.geteuid() != 0):
        raise ExecutionUnavailable("Timeout topology requires an explicitly accepted root Linux worker")
    if (type(engine) is not str or engine not in {"docker", "/usr/bin/docker"}
            or type(container_id) is not str or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
            or type(image_id) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
            or type(fixture_digest) is not str or re.fullmatch(r"[0-9a-f]{64}", fixture_digest) is None
            or type(nonce) is not str or re.fullmatch(r"[0-9a-f]{32}", nonce) is None
            or cancel_check is not None and not callable(cancel_check)):
        raise ExecutionUnavailable("Exact owned fixture worker identities required")
    check = cancel_check or (lambda: None)
    check()
    pid = _inspect(engine, container_id, image_id)
    identity = _process_identity(pid)
    host_inode = _host_netns_inode()
    if identity[2] == host_inode:
        raise ExecutionUnavailable("Owned fixture shares the host network namespace")
    namespace_fd = None
    try:
        namespace_fd = os.open("/proc/" + str(pid) + "/ns/net", os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        namespace = "/proc/" + str(os.getpid()) + "/fd/" + str(namespace_fd)
        def verify():
            check()
            if (os.fstat(namespace_fd).st_ino != identity[2] or _process_identity(pid) != identity
                    or _host_netns_inode() != host_inode or _inspect(engine, container_id, image_id) != pid):
                raise ExecutionUnavailable("Owned process or pinned network namespace changed")
        def operation(program, *args, allow_failure=False):
            verify()
            result = _command(["/usr/bin/nsenter", "--net=" + namespace, "--", "/usr/sbin/" + program, *args],
                              allow_failure=allow_failure)
            verify()
            return result
        verify()
        operation("ip", "route", "add", DESTINATION + "/32", "dev", "lo", "src", "127.0.0.1")
        operation("iptables", "-w", "5", "-A", "OUTPUT", *_RULE)
        route_raw = operation("ip", "-j", "route", "show", "table", "all").stdout
        try:
            routes = json.loads(route_raw)
            if type(routes) is not list or any(type(row) is not dict for row in routes):
                raise ValueError
            exact = [row for row in routes if row.get("dst") in {DESTINATION, DESTINATION + "/32"}]
            if (any(row.get("dst") == "default" for row in routes) or len(exact) != 1
                    or exact[0].get("dev") != "lo" or exact[0].get("prefsrc") != "127.0.0.1"):
                raise ValueError
        except (TypeError, ValueError, UnicodeError):
            raise ExecutionUnavailable("Exact loopback timeout route proof differs") from None
        external = operation("ip", "route", "get", "1.1.1.1", allow_failure=True)
        if external.returncode == 0:
            raise ExecutionUnavailable("Owned fixture unexpectedly has an external route")
        operation("iptables", "-w", "5", "-C", "OUTPUT", *_RULE)
        counters = _counters(operation("iptables", "-w", "5", "-L", "OUTPUT", "-v", "-n", "-x").stdout)
        verify()
        return OwnedTimeoutTopologyReceipt(container_id, image_id, fixture_digest, nonce, pid, identity[1], identity[2], host_inode,
            (DESTINATION + "/32", "dev", "lo", "src", "127.0.0.1"), ("OUTPUT", *_RULE), *counters)
    finally:
        if namespace_fd is not None:
            os.close(namespace_fd)
