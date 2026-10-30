"""Mocked owned-controller lifecycle tests; no subprocesses, target imports or Docker."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_check_requests3362 import receipt as ordinary_receipt
from test_check_requests3362 import spec as private_spec

from atomic_flow import check_requests_execution as controller
from atomic_flow.agents.execution import CommandResult, ExecutionUnavailable, TaskCancelled
from atomic_flow.agents.verify import check_source_revision
from atomic_flow.check_execution import completed_check_receipt, run_check_spec
from atomic_flow.check_fixture_topology import OwnedTimeoutTopologyReceipt
from atomic_flow.check_private_pytest import MARKER

CID = "a" * 64
PROCESS = (1234, "999", 456)
NONCE = "7" * 32


@pytest.fixture
def host(tmp_path, monkeypatch):
    (tmp_path / "requests").mkdir()
    (tmp_path / "requests/__init__.py").write_bytes(b"# public source, never imported\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_requests.py").write_bytes(b"# ordinary test fixture, never executed\n")
    spec = replace(private_spec(), source_revision=check_source_revision(tmp_path))
    h = SimpleNamespace(root=tmp_path, spec=spec, controls=[], executions=[], removed=[], inspections=0,
                        code=0, corrupt_source=False, process=PROCESS, cleanup_ok=True, present_after_cleanup=False)
    monkeypatch.setattr(controller, "platform", SimpleNamespace(system=lambda: "Linux"))
    monkeypatch.setattr(controller, "os", SimpleNamespace(geteuid=lambda: 0))
    monkeypatch.setattr(controller, "container_identity", lambda: "65534:65534")
    monkeypatch.setattr(controller, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex=NONCE)))
    monkeypatch.setattr(controller, "_process_identity", lambda pid: h.process)
    monkeypatch.setattr(controller, "_host_netns_inode", lambda: 789)

    def control(argv, *, home, check):
        check()
        h.controls.append(list(argv))
        if argv[1:3] == ["image", "inspect"]:
            return json.dumps([{"Id": spec.image, "Os": "linux", "Architecture": "amd64",
                                "Config": {"Env": ["PATH=/usr/bin:/bin"]}}])
        if argv[1] == "create":
            from pathlib import Path
            Path(argv[argv.index("--cidfile") + 1]).write_text(CID, encoding="ascii")
            return CID
        if argv[1] == "start":
            assert argv[2] == CID
            return CID
        if argv[1] == "inspect":
            h.inspections += 1
            environment = ["PATH=/usr/bin:/bin", "HOME=/tmp", "PYTHONDONTWRITEBYTECODE=1"]
            return json.dumps([{
                "Id": CID, "Image": spec.image,
                "State": {"Running": True, "Paused": False, "Restarting": False, "Pid": PROCESS[0]},
                "Config": {"User": "65534:65534", "WorkingDir": spec.target_root,
                           "Entrypoint": ["/bin/sleep"], "Cmd": ["infinity"], "Env": environment},
                "HostConfig": {"Init": True, "Privileged": False, "ReadonlyRootfs": True, "NetworkMode": "none",
                               "CapDrop": ["ALL"], "CapAdd": None, "SecurityOpt": ["no-new-privileges"],
                               "Memory": spec.resources.memory_bytes, "NanoCpus": spec.resources.cpus * 1000000000,
                               "PidsLimit": spec.resources.pids, "Binds": None, "VolumesFrom": None,
                               "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=" + str(spec.resources.tmpfs_bytes) + ",mode=1777"}},
                "Mounts": [{"Type": "bind", "Source": str(tmp_path.resolve()), "Destination": spec.target_root, "RW": False}],
            }])
        if argv[1:3] == ["container", "ls"]:
            assert "id=" + CID in argv
            return CID if h.present_after_cleanup else ""
        raise AssertionError("unexpected controller command")

    def topology(**kwargs):
        h.topology_arguments = kwargs
        return OwnedTimeoutTopologyReceipt(CID, spec.image, spec.fixture_identity.digest, NONCE, *PROCESS, 789,
                                           ("10.255.255.1/32", "dev", "lo", "src", "127.0.0.1"),
                                           ("OUTPUT", "-j", "DROP"), 0, 0)

    def bounded(argv, *, home, timeout, limit, check):
        check()
        h.executions.append((list(argv), timeout, limit))
        value = ordinary_receipt(spec, failed=h.code == 1)
        value["exit_code"] = h.code
        if h.corrupt_source:
            (tmp_path / "requests/__init__.py").write_bytes(b"# source changed\n")
        return CommandResult(h.code, MARKER + json.dumps(value), "")

    def remove(engine, identity, home):
        h.removed.append((engine, identity))
        return h.cleanup_ok

    h.control = control
    monkeypatch.setattr(controller, "_control", control)
    monkeypatch.setattr(controller, "_bounded", bounded)
    monkeypatch.setattr(controller, "apply_owned_timeout_topology", topology)
    monkeypatch.setattr(controller, "_remove_container", remove)
    return h


@pytest.mark.parametrize("code", [0, 1])
def test_exact_owned_start_topology_exec_stop_and_absence_proof(host, code):
    host.code = code
    checked, topology = controller.run_requests3362_check(host.spec, host.root)
    assert checked.check_passed is (code == 0)
    assert checked.runner_receipt is not None
    assert checked.runner_receipt["requests3362_fixture"]["servers_stopped"] is True
    assert checked.source_before == checked.source_after == host.spec.source_revision
    assert topology.container_id == CID
    assert host.removed == [(controller.ENGINE, CID)]
    assert host.inspections == 3
    create = next(command for command in host.controls if command[1] == "create")
    assert "--init" in create and "--read-only" in create and "--network=none" in create
    assert "--pull=never" in create and "--entrypoint=/bin/sleep" in create
    assert "--cap-drop=ALL" in create and "--security-opt=no-new-privileges" in create
    assert create[-3:] == ["--", host.spec.image, "infinity"]
    assert "type=bind,src=" + str(host.root.resolve()) + ",dst=" + host.spec.target_root + ",readonly" in create
    assert not any("sudo" in item for item in create)
    assert host.topology_arguments["container_id"] == CID
    assert host.topology_arguments["fixture_digest"] == host.spec.fixture_identity.digest
    command, timeout, bound = host.executions[0]
    assert command[:2] == [controller.ENGINE, "exec"]
    assert command[-3] == host.spec.runtime_identity.launcher and command[-2] == "-c"
    assert CID in command and timeout == host.spec.timeout_seconds
    assert bound == host.spec.resources.output_bytes_per_stream
    assert host.controls[-1][1:3] == ["container", "ls"]


@pytest.mark.parametrize("code", [0, 1])
def test_ordinary_dispatch_revalidates_private_receipt_after_owned_cleanup(host, code):
    host.code = code
    receipt = run_check_spec(host.spec, host.root)
    assert host.removed == [(controller.ENGINE, CID)]
    assert completed_check_receipt(host.spec, receipt)
    assert completed_check_receipt(host.spec, receipt, negative=True) is (code == 1)
    assert receipt.check_passed is (code == 0)


def test_ordinary_completion_refuses_tampered_fixture_lifecycle(host):
    receipt = run_check_spec(host.spec, host.root)
    receipt.runner_receipt["requests3362_fixture"]["servers_stopped"] = False
    assert not completed_check_receipt(host.spec, receipt)


def test_ordinary_dispatch_propagates_cleanup_uncertainty_without_completed_receipt(host):
    host.cleanup_ok = False
    with pytest.raises(ExecutionUnavailable, match="cleanup"):
        run_check_spec(host.spec, host.root)


def test_ordinary_dispatch_refuses_missing_private_fixture(host):
    with pytest.raises(ExecutionUnavailable, match="not implemented"):
        run_check_spec(replace(host.spec, fixture_identity=None), host.root)
    assert not host.controls and not host.executions


def test_nonroot_or_nonlinux_refuses_before_any_command(host, monkeypatch):
    monkeypatch.setattr(controller, "os", SimpleNamespace(geteuid=lambda: 1000))
    with pytest.raises(ExecutionUnavailable, match="root Linux"):
        controller.run_requests3362_check(host.spec, host.root)
    monkeypatch.setattr(controller, "os", SimpleNamespace(geteuid=lambda: 0))
    monkeypatch.setattr(controller, "platform", SimpleNamespace(system=lambda: "Windows"))
    with pytest.raises(ExecutionUnavailable, match="root Linux"):
        controller.run_requests3362_check(host.spec, host.root)
    assert host.controls == host.executions == host.removed == []


def test_stale_source_missing_fixture_and_preexisting_cancel_refuse_before_commands(host):
    with pytest.raises(ValueError):
        controller.run_requests3362_check(replace(host.spec, fixture_identity=None), host.root)
    with pytest.raises(ExecutionUnavailable, match="stale"):
        controller.run_requests3362_check(replace(host.spec, source_revision="f" * 64), host.root)
    with pytest.raises(TaskCancelled):
        controller.run_requests3362_check(host.spec, host.root, cancel_check=lambda: (_ for _ in ()).throw(TaskCancelled("cancelled")))
    assert host.controls == host.executions == []


def test_same_owned_process_must_survive_topology_and_exec(host, monkeypatch):
    original = controller.apply_owned_timeout_topology
    def changed(**kwargs):
        value = original(**kwargs)
        host.process = (PROCESS[0], "1000", PROCESS[2])
        return value
    monkeypatch.setattr(controller, "apply_owned_timeout_topology", changed)
    with pytest.raises(ExecutionUnavailable, match="process"):
        controller.run_requests3362_check(host.spec, host.root)
    assert host.executions == [] and host.removed == [(controller.ENGINE, CID)]


def test_source_changes_and_invalid_lifecycle_cannot_pass(host, monkeypatch):
    host.corrupt_source = True
    with pytest.raises(ExecutionUnavailable, match="source changed"):
        controller.run_requests3362_check(host.spec, host.root)
    assert host.removed == [(controller.ENGINE, CID)]


def test_timeout_or_truncated_output_remains_incomplete_after_cleanup(host, monkeypatch):
    for flags in ({"timed_out": True}, {"stdout_truncated": True}, {"stderr_truncated": True}):
        normal = CommandResult(0, MARKER + json.dumps(ordinary_receipt(host.spec)), "", **flags)
        monkeypatch.setattr(controller, "_bounded", lambda *args, normal=normal, **kwargs: normal)
        checked, _ = controller.run_requests3362_check(host.spec, host.root)
        assert checked.check_passed is False and checked.runner_receipt is None
    assert len(host.removed) == 3


def test_cleanup_failure_or_container_still_present_never_returns_receipt(host):
    host.cleanup_ok = False
    with pytest.raises(ExecutionUnavailable, match="cleanup"):
        controller.run_requests3362_check(host.spec, host.root)
    host.cleanup_ok, host.present_after_cleanup = True, True
    with pytest.raises(ExecutionUnavailable, match="cleanup"):
        controller.run_requests3362_check(host.spec, host.root)
    assert len(host.removed) == 2


def test_create_uncertainty_preserves_immutable_cid_cleanup_or_fails_unknown(host, monkeypatch):
    original = host.control
    def interrupted(argv, **kwargs):
        value = original(argv, **kwargs)
        if argv[1] == "create":
            raise ExecutionUnavailable("creation response uncertain")
        return value
    monkeypatch.setattr(controller, "_control", interrupted)
    with pytest.raises(ExecutionUnavailable, match="creation"):
        controller.run_requests3362_check(host.spec, host.root)
    assert host.removed == [(controller.ENGINE, CID)]

    def no_id(argv, **kwargs):
        if argv[1] == "create":
            raise ExecutionUnavailable("creation failed")
        return original(argv, **kwargs)
    monkeypatch.setattr(controller, "_control", no_id)
    with pytest.raises(ExecutionUnavailable, match="no immutable ID"):
        controller.run_requests3362_check(host.spec, host.root)


def test_policy_inspect_rejects_mutable_workspace_extra_environment_or_missing_init(host, monkeypatch):
    original = host.control
    for change in (
        lambda row: row["Mounts"][0].update(RW=True),
        lambda row: row["HostConfig"].update(Init=False),
        lambda row: row["Config"]["Env"].append("EXTRA=value"),
        lambda row: row["HostConfig"].update(NetworkMode="host"),
        lambda row: row["Config"].update(User="0:0"),
    ):
        def mutated(argv, change=change, **kwargs):
            text = original(argv, **kwargs)
            if argv[1] == "inspect":
                rows = json.loads(text)
                change(rows[0])
                return json.dumps(rows)
            return text
        monkeypatch.setattr(controller, "_control", mutated)
        with pytest.raises(ExecutionUnavailable, match="policy"):
            controller.run_requests3362_check(host.spec, host.root)
    assert not host.executions


def test_bounded_runner_uses_clean_environment_and_retains_truncation_without_target_execution(tmp_path, monkeypatch):
    import io
    seen = {}
    class Process:
        returncode = 0
        stdout = io.BytesIO(b"a" * 100)
        stderr = io.BytesIO(b"")
        def poll(self):
            return self.returncode
        def wait(self, timeout):
            return self.returncode
        def kill(self):
            self.returncode = -9
    def popen(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return Process()
    monkeypatch.setattr(controller, "subprocess", SimpleNamespace(
        Popen=popen, DEVNULL=controller.subprocess.DEVNULL, PIPE=controller.subprocess.PIPE,
        TimeoutExpired=controller.subprocess.TimeoutExpired))
    monkeypatch.setenv("MODEL_PROXY_API_KEY", "synthetic-test-value")
    result = controller._bounded(["/usr/bin/docker", "inspect", CID], home=tmp_path, timeout=1, limit=16, check=lambda: None)
    assert result.stdout == "a" * 16 and result.stdout_truncated
    assert "MODEL_PROXY_API_KEY" not in seen["env"]
    assert seen["stdin"] is controller.subprocess.DEVNULL
    assert seen["start_new_session"] is True
