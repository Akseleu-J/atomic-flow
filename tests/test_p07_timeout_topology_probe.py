"""Fixed topology admission cannot give capabilities or egress to target code."""
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("p07_timeout_topology_probe", ROOT / "tools/p07_timeout_topology_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
IMAGE_ID = "sha256:" + "a" * 64


def config():
    return {"Image": IMAGE_ID, "Mounts": [], "Config": {"User": "65534:65534"}, "State": {"Pid": 123},
            "HostConfig": {"Binds": None, "Privileged": False, "NetworkMode": "none", "CapDrop": ["ALL"],
                "CapAdd": None, "SecurityOpt": ["no-new-privileges"], "ReadonlyRootfs": True,
                "Memory": 256 * 1024**2, "NanoCpus": 1_000_000_000, "PidsLimit": 64}}


@pytest.mark.parametrize("field,value", [("NetworkMode", "bridge"), ("Privileged", True),
    ("CapAdd", ["NET_ADMIN"]), ("CapDrop", []), ("ReadonlyRootfs", False), ("Binds", ["/tmp:/host"]),
    ("Memory", 0), ("SecurityOpt", [])])
def test_worker_policy_refuses_widening(field, value):
    item = config()
    item["HostConfig"][field] = value
    with pytest.raises(ValueError, match="isolation"):
        probe.inspect_policy(item, IMAGE_ID)


def test_fixed_setup_uses_pinned_namespace_only():
    commands = probe.setup_commands("/proc/321/fd/9")
    assert commands[0] == ["/usr/bin/nsenter", "--net=/proc/321/fd/9", "--", "/usr/sbin/ip", "route", "add",
                           "10.255.255.1/32", "dev", "lo", "src", "127.0.0.1"]
    assert commands[1][-len(probe.RULE):] == probe.RULE
    assert not any("--target" in command or "--mount" in command for command in commands)
    with pytest.raises(ValueError):
        probe.namespace_command("/proc/321/ns/net", "ip")
    with pytest.raises(ValueError):
        probe.namespace_command("/proc/321/fd/9", "sh")


def mocked_worker(monkeypatch, *, external=False, cleanup=True, setup_fail=False, identity_changed=False):
    monkeypatch.setattr(probe.platform, "system", lambda: "Linux")
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0, raising=False)
    identities = 0

    def identity(pid):
        nonlocal identities
        identities += 1
        return {"pid": pid, "start_ticks": "4" if identity_changed and identities > 1 else "3", "netns_inode": 8}

    monkeypatch.setattr(probe, "process_identity", identity)
    original_stat, original_open, original_fstat, original_close = Path.stat, probe.os.open, probe.os.fstat, probe.os.close

    def stat(path, *args, **kwargs):
        if path.as_posix() == "/proc/self/ns/net":
            return SimpleNamespace(st_ino=7)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(probe.os, "open", lambda path, *a, **k: 99999 if str(path) == "/proc/123/ns/net" else original_open(path, *a, **k))
    monkeypatch.setattr(probe.os, "fstat", lambda fd: SimpleNamespace(st_ino=8) if fd == 99999 else original_fstat(fd))
    monkeypatch.setattr(probe.os, "close", lambda fd: None if fd == 99999 else original_close(fd))
    calls = []

    def execute(args, timeout=30):
        calls.append(args)
        code, out = 0, b""
        if args[:3] == ["docker", "image", "inspect"]:
            out = json.dumps([{"Id": IMAGE_ID, "Os": "linux", "Architecture": "amd64"}]).encode()
        elif args[:2] == ["docker", "inspect"]:
            out = json.dumps([config()]).encode()
        elif args[:2] == ["docker", "exec"]:
            assert args[-4:-1] == ["-I", "-B", "-c"]
            assert args[-1] == probe.PROBE
            out = json.dumps({"loopback": "real_socket_pass", "blackhole": "socket.timeout", "isolated": 1,
                              "executable": probe.PYTHON}).encode()
        elif args[:3] == ["docker", "container", "ls"]:
            out = b"" if cleanup else b"still-owned"
        elif args[0] == "/usr/bin/nsenter":
            assert args[1].startswith("--net=/proc/") and args[1].endswith("/fd/99999")
            if "/usr/sbin/ip" in args and "-j" in args:
                out = json.dumps([{"dst": "10.255.255.1", "dev": "lo"}]).encode()
            elif "get" in args:
                code = 0 if external else 2
            elif "-L" in args:
                out = b"Chain OUTPUT (policy ACCEPT)\n 1 60 DROP tcp -- * * 0.0.0.0/0 10.255.255.1 tcp dpt:80\n"
            elif "-A" in args and setup_fail:
                code = 1
        return subprocess.CompletedProcess(args, code, stdout=out, stderr=b"")

    monkeypatch.setattr(probe, "execute", execute)
    return calls


def test_synthetic_qualification_is_not_calibration(monkeypatch, tmp_path):
    calls = mocked_worker(monkeypatch)
    report = probe.qualify(tmp_path / "fresh")
    assert report["status"] == "synthetic_timeout_topology_qualified"
    assert report["calibrated"] is False and report["verified_success"] is False
    assert report["model_calls"] == 0 and report["target_imports_or_tests"] is False
    assert report["cleanup_confirmed"] is True
    create = next(args for args in calls if args[:2] == ["docker", "create"])
    assert "--cap-add" not in create and "--privileged" not in create
    assert "--network" in create and create[create.index("--network") + 1] == "none"
    assert not any("pytest" in str(args) or "import requests" in str(args) for args in calls)


@pytest.mark.parametrize("options,status", [({"external": True}, "unavailable"),
    ({"cleanup": False}, "cleanup_uncertain"), ({"setup_fail": True}, "unavailable"),
    ({"identity_changed": True}, "unavailable")])
def test_failure_cannot_qualify(monkeypatch, tmp_path, options, status):
    calls = mocked_worker(monkeypatch, **options)
    report = probe.qualify(tmp_path / "fresh")
    assert report["status"] == status
    assert any(args[:3] == ["docker", "rm", "-f"] for args in calls)
    assert report["verified_success"] is False


def test_existing_output_is_not_reused(monkeypatch, tmp_path):
    mocked_worker(monkeypatch)
    with pytest.raises(ValueError, match="fresh output"):
        probe.qualify(tmp_path)


def test_actual_numeric_protocol_counter_is_tcp():
    # Captured from CI40448432207, preserved in timeout-topology-v1/result.json.
    actual = ("Chain OUTPUT (policy ACCEPT 9 packets, 501 bytes)\n"
              "    pkts      bytes target     prot opt in     out     source               destination\n"
              "       1       60 DROP       6    --  *      *       0.0.0.0/0            10.255.255.1         tcp dpt:80\n")
    assert probe.dropped_tcp_packets(actual) == 1
    assert probe.dropped_tcp_packets(actual.replace("DROP       6", "DROP     tcp")) == 1


@pytest.mark.parametrize("row", [
    "0 60 DROP 6 -- * * 0.0.0.0/0 10.255.255.1 tcp dpt:80",
    "1 0 DROP 6 -- * * 0.0.0.0/0 10.255.255.1 tcp dpt:80",
    "1 60 DROP 17 -- * * 0.0.0.0/0 10.255.255.1 tcp dpt:80",
    "1 60 DROP 6 -- * * 0.0.0.0/0 10.255.255.10 tcp dpt:80",
    "1 60 DROP 6 -- * * 0.0.0.0/0 10.255.255.1 tcp dpt:800",
    "1 60 ACCEPT 6 -- * * 0.0.0.0/0 10.255.255.1 tcp dpt:80",
    "1 60 DROP 6 -- * * 0.0.0.0/0 10.255.255.1 tcp dpt:80 extra",
    "1 60 DROP 6 -- * * 10.0.0.0/8 10.255.255.1 tcp dpt:80",
])
def test_counter_requires_positive_exact_tcp_destination_rule(row):
    assert probe.dropped_tcp_packets(row) == 0
