"""Mocked host topology commands only; never enter namespaces or run probes."""
import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from atomic_flow import check_fixture_topology as topology
from atomic_flow.agents.execution import ExecutionUnavailable

CID = "a" * 64
IMAGE = "sha256:" + "b" * 64
FIXTURE = "c" * 64
NONCE = "d" * 32


@pytest.fixture
def worker(monkeypatch):
    config = {"Id": CID, "Image": IMAGE, "Config": {"User": "65534:65534"},
              "State": {"Running": True, "Paused": False, "Restarting": False, "Pid": 123},
              "HostConfig": {"Privileged": False, "ReadonlyRootfs": True, "NetworkMode": "none",
                             "CapDrop": ["ALL"], "CapAdd": None, "SecurityOpt": ["no-new-privileges"],
                             "Memory": 1024**3, "NanoCpus": 2_000_000_000, "PidsLimit": 128}}
    state = SimpleNamespace(config=config, commands=[], closes=[], opens=[], birth="456", inode=789,
                            host_inode=555, fd_inode=789, route_dev="lo", route_src="127.0.0.1",
                            external_exit=2, counter_rows=1, namespace_started=False, change_after_command=False)
    monkeypatch.setattr(topology.platform, "system", lambda: "Linux")
    monkeypatch.setattr(topology.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(topology, "_host_netns_inode", lambda: state.host_inode)
    monkeypatch.setattr(topology, "_process_identity", lambda pid: (pid, "changed" if state.change_after_command and state.namespace_started else state.birth, state.inode))
    monkeypatch.setattr(topology.os, "open", lambda path, flags: state.opens.append((path, flags)) or 99)
    monkeypatch.setattr(topology.os, "fstat", lambda fd: SimpleNamespace(st_ino=state.fd_inode))
    monkeypatch.setattr(topology.os, "close", lambda fd: state.closes.append(fd))
    monkeypatch.setattr(topology.os, "getpid", lambda: 42)
    def run(argv, **kwargs):
        state.commands.append((argv, kwargs))
        assert kwargs == {"stdin": topology.subprocess.DEVNULL, "capture_output": True, "timeout": 15,
                          "check": False, "env": {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}}
        code, output = 0, b""
        if argv[:2] == ["docker", "inspect"]:
            output = json.dumps([state.config]).encode()
        else:
            assert argv[:4] == ["/usr/bin/nsenter", "--net=/proc/42/fd/99", "--", argv[3]]
            assert argv[3] in {"/usr/sbin/ip", "/usr/sbin/iptables"}
            state.namespace_started = True
            if argv[4:] == ["-j", "route", "show", "table", "all"]:
                output = json.dumps([{"dst": "10.255.255.1", "dev": state.route_dev, "prefsrc": state.route_src}]).encode()
            elif argv[4:] == ["route", "get", "1.1.1.1"]:
                code = state.external_exit
            elif argv[4:] == ["-w", "5", "-L", "OUTPUT", "-v", "-n", "-x"]:
                output = b"Chain OUTPUT (policy ACCEPT 0 packets, 0 bytes)\n pkts bytes target prot opt in out source destination\n"
                output += b"0 0 DROP tcp -- * * 0.0.0.0/0 10.255.255.1 tcp dpt:80\n" * state.counter_rows
        return SimpleNamespace(returncode=code, stdout=output, stderr=b"")
    monkeypatch.setattr(topology.subprocess, "run", run)
    return state


def apply(**changes):
    return topology.apply_owned_timeout_topology(**({"engine": "docker", "container_id": CID,
        "image_id": IMAGE, "fixture_digest": FIXTURE, "nonce": NONCE} | changes))


def test_fixed_owned_namespace_route_and_drop_proof_without_target_execution(worker):
    receipt = apply()
    assert receipt.container_id == CID and receipt.image_id == IMAGE and receipt.fixture_digest == FIXTURE
    assert receipt.nonce == NONCE and receipt.pid == 123 and receipt.start_ticks == "456"
    assert receipt.netns_inode == 789 and receipt.host_netns_inode == 555
    assert receipt.route == ("10.255.255.1/32", "dev", "lo", "src", "127.0.0.1")
    assert receipt.rule == ("OUTPUT", "-d", "10.255.255.1/32", "-p", "tcp", "--dport", "80", "-j", "DROP")
    assert receipt.dropped_packets == receipt.dropped_bytes == 0
    assert receipt.status == "topology_applied_only"
    assert worker.opens[0][0] == "/proc/123/ns/net" and worker.closes == [99]
    commands = [argv for argv, _ in worker.commands if argv[0] == "/usr/bin/nsenter"]
    assert len(commands) == 6
    assert commands[0][4:] == ["route", "add", "10.255.255.1/32", "dev", "lo", "src", "127.0.0.1"]
    assert commands[1][4:] == ["-w", "5", "-A", "OUTPUT", *receipt.rule[1:]]
    assert all("exec" not in argv and "sudo" not in argv for argv, _ in worker.commands)
    with pytest.raises(FrozenInstanceError):
        receipt.pid = 999


@pytest.mark.parametrize("change", [
    {"engine": "/bin/sh"}, {"engine": "docker --privileged"}, {"container_id": "a" * 12},
    {"container_id": "A" * 64}, {"image_id": "repo:latest"}, {"fixture_digest": "c" * 63},
    {"nonce": "d" * 31}, {"nonce": None}, {"cancel_check": True},
])
def test_invalid_host_identity_never_inspects_or_mutates(worker, change):
    with pytest.raises(ExecutionUnavailable, match="identities required"):
        apply(**change)
    assert worker.commands == [] and worker.opens == []


@pytest.mark.parametrize("system,uid", [("Windows", 0), ("Linux", 1000)])
def test_no_unprivileged_or_nonlinux_fallback(worker, monkeypatch, system, uid):
    monkeypatch.setattr(topology.platform, "system", lambda: system)
    monkeypatch.setattr(topology.os, "geteuid", lambda: uid)
    with pytest.raises(ExecutionUnavailable, match="root Linux worker"):
        apply()
    assert worker.commands == []


@pytest.mark.parametrize("group,key,value", [
    (None, "Id", "0" * 64), (None, "Image", "sha256:" + "0" * 64),
    ("State", "Running", False), ("State", "Paused", True), ("State", "Restarting", True),
    ("State", "Pid", 1), ("Config", "User", "root"), ("Config", "User", "0:0"),
    ("HostConfig", "NetworkMode", "host"), ("HostConfig", "Privileged", True),
    ("HostConfig", "ReadonlyRootfs", False), ("HostConfig", "CapDrop", []),
    ("HostConfig", "CapAdd", ["NET_ADMIN"]), ("HostConfig", "SecurityOpt", []),
    ("HostConfig", "Memory", 0), ("HostConfig", "Memory", 1024**3 + 1),
    ("HostConfig", "NanoCpus", 0), ("HostConfig", "NanoCpus", 2_000_000_001),
    ("HostConfig", "PidsLimit", 129), ("HostConfig", "PidsLimit", True),
])
def test_unsafe_container_refuses_before_opening_namespace(worker, group, key, value):
    (worker.config if group is None else worker.config[group])[key] = value
    with pytest.raises(ExecutionUnavailable, match="identity or isolation policy"):
        apply()
    assert worker.opens == []
    assert not any(argv[0] == "/usr/bin/nsenter" for argv, _ in worker.commands)


def test_host_network_namespace_never_receives_commands(worker):
    worker.inode = worker.host_inode
    with pytest.raises(ExecutionUnavailable, match="shares the host"):
        apply()
    assert worker.opens == []


def test_namespace_fd_mismatch_closes_and_never_mutates(worker):
    worker.fd_inode = 1234
    with pytest.raises(ExecutionUnavailable, match="pinned network namespace changed"):
        apply()
    assert worker.closes == [99]
    assert not worker.namespace_started


def test_process_birth_drift_after_fixed_command_stops_and_closes(worker):
    worker.change_after_command = True
    with pytest.raises(ExecutionUnavailable, match="process or pinned"):
        apply()
    assert worker.closes == [99]
    assert len([argv for argv, _ in worker.commands if argv[0] == "/usr/bin/nsenter"]) == 1


@pytest.mark.parametrize("field,value", [("route_dev", "eth0"), ("route_src", "10.0.0.1"),
                                        ("external_exit", 0), ("counter_rows", 0), ("counter_rows", 2)])
def test_route_or_rule_proof_mismatch_refuses_with_closed_fd(worker, field, value):
    setattr(worker, field, value)
    with pytest.raises(ExecutionUnavailable):
        apply()
    assert worker.closes == [99]


def test_cancellation_always_closes_pinned_namespace_fd(worker):
    def cancel():
        if worker.opens:
            raise RuntimeError("cancelled by host")
    with pytest.raises(RuntimeError, match="cancelled by host"):
        apply(cancel_check=cancel)
    assert worker.closes == [99] and not worker.namespace_started


def test_counter_parser_retains_exact_zero_or_observed_packet_counts():
    assert topology._counters(b"3 180 DROP 6 -- * * 0.0.0.0/0 10.255.255.1/32 tcp dpt:80\n") == (3, 180)
    for line in (b"3 180 ACCEPT tcp -- * * 0.0.0.0/0 10.255.255.1 tcp dpt:80\n",
                 b"3 180 DROP tcp -- * * 0.0.0.0/0 10.255.255.1 tcp dpt:443\n"):
        with pytest.raises(ExecutionUnavailable):
            topology._counters(line)
