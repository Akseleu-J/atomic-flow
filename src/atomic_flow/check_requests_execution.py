"""Owned Requests Docker lifecycle: qualified3362 plus explicit function36 path.

The caller must separately admit actual runtime/fixture provisioning evidence.
This controller verifies its fixed worker/source/lifecycle observations and
returns only after immutable-ID cleanup and absence proof. It never uses sudo,
provider credentials, arbitrary commands or a host service. The explicit
function36 wrapper separately validates its sole typed CA overlay before/after.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from atomic_flow.agents.execution import (
    CommandResult,
    ExecutionUnavailable,
    _remove_container,
    clean_environment,
    container_identity,
)
from atomic_flow.agents.verify import check_source_revision
from atomic_flow.check_execution import CheckReceipt, admit_check_spec
from atomic_flow.check_fixture_topology import (
    OwnedTimeoutTopologyReceipt,
    _host_netns_inode,
    _process_identity,
    apply_owned_timeout_topology,
)
from atomic_flow.check_requests3362 import requests3362_args, requests3362_receipt
from atomic_flow.check_spec import CheckSpec
from atomic_flow.config import ExecutionConfig

ENGINE = "/usr/bin/docker"
_CONTROL_BYTES = 64 * 1024


def _bounded(argv, *, home, timeout, limit, check):
    """Run only controller-built argv with bounded stream drains and cancellation."""
    check()
    tails = [bytearray(), bytearray()]
    truncated = [False, False]
    broken = threading.Event()

    def drain(stream, index):
        try:
            read = getattr(stream, "read1", stream.read)
            while block := read(4096):
                tails[index].extend(block)
                if len(tails[index]) > limit:
                    truncated[index] = True
                    del tails[index][:-limit]
        except Exception:
            broken.set()
        finally:
            stream.close()

    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=clean_environment(home), start_new_session=True)
    except OSError:
        raise ExecutionUnavailable("Fixed Requests worker command could not start") from None
    workers = [threading.Thread(target=drain, args=(process.stdout, 0), daemon=True),
               threading.Thread(target=drain, args=(process.stderr, 1), daemon=True)]
    for worker in workers:
        worker.start()
    expired = False
    try:
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            check()
            if time.monotonic() >= deadline:
                expired = True
                process.kill()
                break
            time.sleep(0.025)
        process.wait(timeout=5)
    except BaseException:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise
    finally:
        for worker in workers:
            worker.join(timeout=2)
    if broken.is_set() or any(worker.is_alive() for worker in workers):
        raise ExecutionUnavailable("Requests worker output collection is incomplete")
    try:
        stdout, stderr = (bytes(tail).decode("utf-8", errors="strict") for tail in tails)
    except UnicodeError:
        raise ExecutionUnavailable("Requests worker output is not complete UTF-8") from None
    return CommandResult(process.returncode, stdout, stderr, expired, *truncated)


def _control(argv, *, home, check):
    result = _bounded(argv, home=home, timeout=15, limit=_CONTROL_BYTES, check=check)
    if result.returncode or result.timed_out or result.stdout_truncated or result.stderr_truncated:
        raise ExecutionUnavailable("Fixed Requests worker control command failed")
    return result.stdout


def _rows(raw):
    try:
        rows = json.loads(raw)
        if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
            raise ValueError
        return rows[0]
    except (TypeError, ValueError):
        raise ExecutionUnavailable("Requests worker inspect evidence is invalid") from None


def _environment(image):
    environment = image.get("Config", {}).get("Env") or []
    if type(environment) is not list or any(type(item) is not str or "=" not in item for item in environment):
        raise ExecutionUnavailable("Requests image environment is invalid")
    values = {}
    for item in environment:
        key, value = item.split("=", 1)
        if key in values or re.search(r"(?i)(?:MODEL_PROXY|KAGGLE|API_KEY|PASSWORD|SECRET|TOKEN)", key):
            raise ExecutionUnavailable("Requests image contains unadmitted credential environment")
        values[key] = value
    values.update(HOME="/tmp", PYTHONDONTWRITEBYTECODE="1")
    return values


def _inspect(container_id, spec, workspace, user, environment, *, home, check, expected_process=None):
    value = _rows(_control([ENGINE, "inspect", container_id], home=home, check=check))
    host, running, config = value.get("HostConfig", {}), value.get("State", {}), value.get("Config", {})
    mounts = value.get("Mounts")
    if type(mounts) is not list:
        raise ExecutionUnavailable("Requests worker mounts are unavailable")
    binds = [mount for mount in mounts if mount.get("Type") == "bind"]
    extra = [mount for mount in mounts if mount.get("Type") != "bind"]
    env = config.get("Env") or []
    try:
        actual_env = dict(item.split("=", 1) for item in env)
    except (TypeError, ValueError):
        raise ExecutionUnavailable("Requests worker environment differs") from None
    resources = spec.resources
    function = spec.fixture_identity is not None and spec.fixture_identity.variant == "function36-v3"
    expected_hosts = [host + ":" + address for host, address in spec.fixture_identity.host_mappings] if function else []
    expected_sysctls = {"net.ipv4.ip_unprivileged_port_start": "0"} if function else {}
    if (value.get("Id") != container_id or value.get("Image") != spec.image
            or running.get("Running") is not True or running.get("Paused") is not False
            or running.get("Restarting") is not False or config.get("User") != user
            or config.get("WorkingDir") != spec.target_root or config.get("Entrypoint") != ["/bin/sleep"]
            or config.get("Cmd") != ["infinity"] or actual_env != environment or len(env) != len(environment)
            or host.get("Init") is not True or host.get("Privileged") is not False
            or host.get("ReadonlyRootfs") is not True or host.get("NetworkMode") != "none"
            or host.get("CapDrop") != ["ALL"] or host.get("CapAdd")
            or "no-new-privileges" not in host.get("SecurityOpt", [])
            or host.get("Memory") != resources.memory_bytes or host.get("NanoCpus") != resources.cpus * 1_000_000_000
            or host.get("PidsLimit") != resources.pids or host.get("Binds") or host.get("VolumesFrom")
            or sorted(host.get("ExtraHosts") or []) != expected_hosts or (host.get("Sysctls") or {}) != expected_sysctls
            or len(binds) != 1 or binds[0].get("Source") != str(workspace.resolve())
            or binds[0].get("Destination") != spec.target_root or binds[0].get("RW") is not False
            or any(mount.get("Type") != "tmpfs" or mount.get("Destination") != "/tmp" for mount in extra)
            or host.get("Tmpfs") != {"/tmp": "rw,noexec,nosuid,size=" + str(resources.tmpfs_bytes) + ",mode=1777"}):
        raise ExecutionUnavailable("Requests worker immutable identity or isolation policy differs")
    process = _process_identity(running.get("Pid"))
    if process[2] == _host_netns_inode() or expected_process is not None and process != expected_process:
        raise ExecutionUnavailable("Requests worker process or network namespace changed")
    return process


def _cid(cidfile):
    if cidfile.is_symlink() or not cidfile.is_file() or cidfile.stat().st_size > 65:
        raise ExecutionUnavailable("Owned Requests container ID file unavailable")
    value = cidfile.read_text(encoding="ascii").strip()
    if re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ExecutionUnavailable("Owned Requests container ID is invalid")
    return value


def run_requests3362_check(spec: CheckSpec, workspace: Path, *, cancel_check=None
                           ) -> tuple[CheckReceipt, OwnedTimeoutTopologyReceipt]:
    return _run_requests_check(spec, workspace, cancel_check=cancel_check)


def _run_requests_check(spec: CheckSpec, workspace: Path, *, cancel_check=None, _function_overlay=None
                        ) -> tuple[CheckReceipt, OwnedTimeoutTopologyReceipt]:
    """Run exact admitted Requests3362 check in its owned root-worker namespace."""
    if platform.system() != "Linux" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise ExecutionUnavailable("Requests3362 requires an explicitly accepted root Linux worker")
    if cancel_check is not None and not callable(cancel_check):
        raise ExecutionUnavailable("Host cancellation callback required")
    check = cancel_check or (lambda: None)
    check()
    nonce = uuid.uuid4().hex
    if _function_overlay is None:
        launcher_args = requests3362_args(spec, nonce)
        parse_receipt = requests3362_receipt
    else:
        from atomic_flow.check_requests_function import function_args, function_receipt
        launcher_args = function_args(spec, nonce, _function_overlay)
        parse_receipt = function_receipt
    workspace = Path(workspace).absolute()
    mount_source = str(workspace.resolve())
    if any(character in mount_source for character in (",", "\n", "\r", "\0")):
        raise ExecutionUnavailable("Requests workspace cannot be represented safely as a bind mount")
    execution = ExecutionConfig(mode="docker", profile="checks", image=spec.image,
                                output_bytes=spec.resources.output_bytes_per_stream)
    before = admit_check_spec(spec, workspace, execution, spec.timeout_seconds, list(spec.argv[1:]), cancel_check=check)
    user = container_identity()
    if re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", user) is None:
        raise ExecutionUnavailable("Requests worker must use a non-root container identity")
    with tempfile.TemporaryDirectory(prefix="atomic-requests3362-") as directory:
        home = Path(directory)
        cidfile = home / "owned-container.cid"
        container_id = None
        attempted_create = False
        try:
            image = _rows(_control([ENGINE, "image", "inspect", spec.image], home=home, check=check))
            if image.get("Id") != spec.image or image.get("Os") != "linux" or image.get("Architecture") != "amd64":
                raise ExecutionUnavailable("Requests actual image differs from its admitted immutable ID")
            environment = _environment(image)
            resources = spec.resources
            create = [ENGINE, "create", "--cidfile", str(cidfile), "--init", "--pull=never", "--network=none",
                      "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                      "--memory=" + str(resources.memory_bytes), "--cpus=" + str(resources.cpus),
                      "--pids-limit=" + str(resources.pids), "--user=" + user,
                      "--tmpfs=/tmp:rw,noexec,nosuid,size=" + str(resources.tmpfs_bytes) + ",mode=1777",
                      "--workdir=" + spec.target_root, "--mount",
                      "type=bind,src=" + mount_source + ",dst=" + spec.target_root + ",readonly",
                      "--env=HOME=/tmp", "--env=PYTHONDONTWRITEBYTECODE=1",
                      "--entrypoint=/bin/sleep", "--", spec.image, "infinity"]
            if _function_overlay is not None:
                transport = ["--sysctl=net.ipv4.ip_unprivileged_port_start=0",
                             *["--add-host=" + host + ":" + address for host, address in spec.fixture_identity.host_mappings]]
                create[2:2] = transport
            attempted_create = True
            _control(create, home=home, check=check)
            container_id = _cid(cidfile)
            _control([ENGINE, "start", container_id], home=home, check=check)
            process = _inspect(container_id, spec, workspace, user, environment, home=home, check=check)
            topology = apply_owned_timeout_topology(
                engine=ENGINE, container_id=container_id, image_id=spec.image,
                fixture_digest=spec.fixture_identity.digest, nonce=nonce, cancel_check=check)
            if (type(topology) is not OwnedTimeoutTopologyReceipt or topology.container_id != container_id
                    or topology.image_id != spec.image or topology.fixture_digest != spec.fixture_identity.digest
                    or topology.nonce != nonce or (topology.pid, topology.start_ticks, topology.netns_inode) != process):
                raise ExecutionUnavailable("Requests topology is not bound to its actual owned process")
            _inspect(container_id, spec, workspace, user, environment, home=home, check=check, expected_process=process)
            if check_source_revision(workspace, check) != before:
                raise ExecutionUnavailable("Requests source changed before ordinary execution")
            command = [ENGINE, "exec", "--workdir=" + spec.target_root, "--user=" + user,
                       "--env=HOME=/tmp", "--env=PYTHONDONTWRITEBYTECODE=1", container_id,
                       spec.runtime_identity.launcher, *launcher_args]
            result = _bounded(command, home=home, timeout=spec.timeout_seconds,
                              limit=resources.output_bytes_per_stream, check=check)
            _inspect(container_id, spec, workspace, user, environment, home=home, check=check, expected_process=process)
            after = check_source_revision(workspace, check)
            if after != before:
                raise ExecutionUnavailable("Requests source changed during ordinary execution")
            parsed = parse_receipt(result, spec, nonce)
            receipt = CheckReceipt(spec.digest, before, after, parsed is not None and result.returncode == 0,
                                   parsed, result)
        finally:
            if container_id is None and cidfile.exists():
                container_id = _cid(cidfile)
            if container_id is not None:
                removed = _remove_container(ENGINE, container_id, home)
                absent = _control([ENGINE, "container", "ls", "-a", "--no-trunc", "--filter", "id=" + container_id,
                                   "--format", "{{.ID}}"], home=home, check=lambda: None)
                if not removed or absent.strip():
                    raise ExecutionUnavailable("Requests owned container cleanup is unresolved")
            elif attempted_create:
                raise ExecutionUnavailable("Requests container creation outcome has no immutable ID; reconcile worker")
        return receipt, topology
