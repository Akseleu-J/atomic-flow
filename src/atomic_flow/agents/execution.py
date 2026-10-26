"""Bounded command execution. Local mode is explicitly NOT a security sandbox."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType

from atomic_flow.config import ExecutionConfig
from atomic_flow.output import sanitize_output


class ExecutionUnavailable(RuntimeError):
    pass


class TaskCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionRequirements:
    """Host-owned mandatory controls. Unsatisfied requirements always refuse."""
    filesystem_isolation: bool = False
    network_isolation: bool = False
    workspace_read_only: bool = False
    resource_limits: bool = False
    owned_process_tree: bool = False


def execution_manifest(config: ExecutionConfig, requirements: ExecutionRequirements | None = None, *, probe=False) -> dict:
    """Resolve an explicit executor without starting it or installing anything.

    Docker capabilities describe its configured contract, not remote attestation.
    The local interpreter identity is descriptive, not dependency provisioning.
    """
    execution_profile(config.profile)
    docker = config.mode == "docker"
    caps = ExecutionRequirements(docker, docker, docker and config.profile == "read-only", docker,
                                 docker or os.name == "nt")
    if not docker and config.profile != "checks":
        raise ExecutionUnavailable("The selected execution profile requires Docker; local mode is unsandboxed")
    if requirements is not None:
        if not isinstance(requirements, ExecutionRequirements):
            raise ExecutionUnavailable("Execution requirements must be host-defined")
        missing = [name for name, required in asdict(requirements).items() if required and not getattr(caps, name)]
        if missing:
            raise ExecutionUnavailable("Executor lacks required capabilities: " + ", ".join(missing))
    if probe and docker and shutil.which("docker") is None:
        raise ExecutionUnavailable("Docker unavailable; no local fallback. See docs/EXECUTION.md.")
    runtime = {"image": config.image} if docker else {"python": str(Path(sys.executable).resolve()),
                                                       "version": platform.python_version(), "platform": sys.platform}
    result = {"schema_version": 1, "executor": "docker" if docker else "trusted-local", "profile": config.profile,
              "capabilities": asdict(caps), "runtime": runtime,
              "runtime_immutable": docker and "@sha256:" in config.image}
    result["digest"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False


@dataclass(frozen=True)
class ExecutionProfile:
    """Host-defined Docker bounds; repository configuration can only select a name."""

    name: str
    workspace_read_only: bool
    pids: int
    memory: str
    cpus: int
    tmpfs_size: str


EXECUTION_PROFILES = MappingProxyType({
    "checks": ExecutionProfile("checks", False, 128, "1g", 2, "128m"),
    "read-only": ExecutionProfile("read-only", True, 64, "512m", 1, "64m"),
})


# -I removes cwd/PYTHONPATH/user-site from checker discovery. Import the installed
# package before permitting project imports required by pytest's test modules.
# Source tests/plugins still execute project code: this is launcher integrity,
# not a claim that hostile tests cannot lie or mutate their own interpreter.
_CHECKER_BOOTSTRAP = """
import importlib, os, runpy, sys, tempfile
tool, arguments = sys.argv[1], sys.argv[2:]
if tool == 'pytest':
    import pytest
    # Timestamp/size-valid project .pyc can describe an earlier edit. -B alone
    # only prevents writes and still reads that cache. Use a fresh private
    # prefix for each check before permitting any project module imports.
    sys.pycache_prefix = tempfile.mkdtemp(prefix='atomic-check-bytecode-')
    sys.dont_write_bytecode = True
    class Collection:
        collected = 0
        def pytest_sessionfinish(self, session, exitstatus):
            self.collected = session.testscollected
    collection = Collection()
    sys.path.insert(0, os.getcwd())
    # pytest already defaults to cwd when no target is supplied. Appending cwd
    # would silently widen explicit matching-test selections to the whole repo.
    targets = arguments or [os.getcwd()]
    code = int(pytest.main(targets, plugins=[collection]))
    raise SystemExit(5 if code == 0 and collection.collected == 0 else code)
importlib.import_module(tool)
sys.argv = [tool, *arguments]
runpy.run_module(tool, run_name='__main__', alter_sys=True)
"""


def _trusted_checker_args(args: list[str]) -> list[str]:
    if len(args) >= 2 and args[0] == "-m" and args[1] in {"pytest", "ruff", "mypy"}:
        return ["-I", "-c", _CHECKER_BOOTSTRAP, args[1], *args[2:]]
    return args


def execution_profile(name: str) -> ExecutionProfile:
    try:
        return EXECUTION_PROFILES[name]
    except (KeyError, TypeError):
        raise ExecutionUnavailable("Unknown execution profile") from None


@contextmanager
def checked_workspace(project: Path, *, private_workspace=False, cancel_check=None):
    """Model-facing checks use admitted copies; never publish writes by tests."""
    if private_workspace:
        yield project
        return
    from atomic_flow.agents.changeset import ChangeSet
    snapshot = ChangeSet.create(project, cancel_check=cancel_check or (lambda: None))
    try:
        yield snapshot.workspace
        snapshot.check_original()
    finally:
        snapshot.abort()


def clean_environment(home: Path) -> dict[str, str]:
    # Do not forward provider credentials, PYTHONPATH or the agent's private HOME.
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(HOME=str(home), USERPROFILE=str(home), TMP=str(home), TEMP=str(home),
               TMPDIR=str(home), PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    return env


def container_identity() -> str:
    """Keep private Unix workspaces accessible without chmod or running as root."""
    uid = os.getuid() if hasattr(os, "getuid") else 0
    gid = os.getgid() if hasattr(os, "getgid") else 0
    if uid == 0:
        return "65534:65534"
    return f"{uid}:{gid if gid != 0 else 65534}"


def docker_command(engine: str, image: str, workspace: Path, name: str, args: list[str], *,
                   profile: str = "checks", container_id_file: Path | None = None,
                   host_spec=None, host_spec_nonce: str | None = None) -> list[str]:
    bounds = execution_profile(profile)
    root = str(workspace.resolve())
    if any(character in root for character in (",", "\n", "\r", "\0")):
        raise ExecutionUnavailable("Workspace path cannot be represented safely as a Docker bind mount")
    target_root = "/workspace"
    interpreter = "python"
    checker = _trusted_checker_args(args)
    resource_args = [f"--pids-limit={bounds.pids}", f"--memory={bounds.memory}", f"--cpus={bounds.cpus}"]
    tmpfs_size = bounds.tmpfs_size
    if host_spec is not None:
        from atomic_flow.check_execution import checker_args
        from atomic_flow.check_spec import CheckSpec
        if (type(host_spec) is not CheckSpec or host_spec.image != image or profile != "checks"
                or args != list(host_spec.argv[1:])):
            raise ExecutionUnavailable("Docker command differs from the host CheckSpec")
        target_root, interpreter = host_spec.target_root, host_spec.interpreter
        checker = checker_args(host_spec, host_spec_nonce)
        resources = host_spec.resources
        resource_args = [f"--pids-limit={resources.pids}", f"--memory={resources.memory_bytes}", f"--cpus={resources.cpus}"]
        tmpfs_size = str(resources.tmpfs_bytes)
    elif host_spec_nonce is not None:
        raise ExecutionUnavailable("CheckSpec nonce requires a host specification")
    mount = f"type=bind,src={root},dst={target_root}" + (",readonly" if bounds.workspace_read_only else "")
    cid_args = ["--cidfile", str(container_id_file.absolute())] if container_id_file is not None else []
    return [engine, "run", *cid_args, "--name", name, "--rm", "--pull=never", "--network=none",
            "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            *resource_args,
            f"--user={container_identity()}",
            f"--tmpfs=/tmp:rw,noexec,nosuid,size={tmpfs_size},mode=1777", f"--workdir={target_root}",
            "--mount", mount,
            "--env=HOME=/tmp", "--env=PYTHONDONTWRITEBYTECODE=1",
            "--env=PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "--", image, interpreter, *checker]


def _kill_tree(proc: subprocess.Popen):
    uncertain = False
    try:
        if os.name == "nt":
            # Best effort; detached children can escape. Do not let a failed helper
            # skip termination of the Popen we actually own, or retain helper pipes.
            try:
                result = subprocess.run([str(Path(os.environ["SYSTEMROOT"]) / "System32" / "taskkill.exe"),
                                "/PID", str(proc.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                uncertain = result.returncode != 0
            except (OSError, subprocess.TimeoutExpired):
                uncertain = True
            # Parent exit proves only parent exit. A failed/absent tree helper
            # (including 128) leaves descendants unknown even after fallback kill.
            # Local checks use WindowsJob accounting instead of this best effort
            # helper; generic host clients must retain failure uncertainty.
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    uncertain = True
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
    if uncertain:
        raise ExecutionUnavailable("Windows process-tree cleanup could not be confirmed; effects remain unknown") from None


def _remove_container(engine: str, identity: str, home: Path) -> bool:
    """Stop an owned container or prove absence; daemon failure is not absence."""
    try:
        result = subprocess.run([engine, "rm", "-f", identity], capture_output=True, timeout=15,
                                env=clean_environment(home))
        if result.returncode == 0:
            return True
        filter_value = "id=" + identity if re.fullmatch(r"[a-f0-9]{64}", identity) else "name=^/" + identity + "$"
        absent = subprocess.run([engine, "container", "ls", "-a", "--no-trunc", "--filter", filter_value,
                                 "--format", "{{.ID}}"], capture_output=True, text=True, timeout=15,
                                env=clean_environment(home))
        return absent.returncode == 0 and not absent.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_python(args: list[str], project: Path, *, config: ExecutionConfig | None = None,
               timeout: float = 120, cancel_check: Callable[[], None] | None = None,
               output_callback: Callable[[str, bytes], None] | None = None,
               container_name: str | None = None, container_id_file: Path | None = None,
               requirements: ExecutionRequirements | None = None,
               host_spec=None, host_spec_nonce: str | None = None) -> CommandResult:
    """Execute Python arguments; docker mode fails closed without an engine/image.

    Cancel checks raise TaskCancelled. Output retains only a bounded tail per stream.
    Local mode reduces accidental credential inheritance but permits host file/network access.
    """
    config = config or ExecutionConfig()
    if host_spec is not None:
        from atomic_flow.check_execution import admit_check_spec, checker_args
        admit_check_spec(host_spec, project, config, timeout, args, cancel_check=cancel_check)
        checker_args(host_spec, host_spec_nonce)
    elif host_spec_nonce is not None:
        raise ExecutionUnavailable("CheckSpec nonce requires a host specification")
    execution_manifest(config, requirements, probe=True)
    check = cancel_check or (lambda: None)
    check()
    engine = None
    if container_name is not None and re.fullmatch(r"atomic-job-[a-f0-9]{24}", container_name) is None:
        raise ExecutionUnavailable("Invalid durable container name")
    if container_id_file is not None:
        if container_name is None:
            raise ExecutionUnavailable("Container identity file requires a durable job name")
        for part in (container_id_file, *container_id_file.parents):
            if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
                raise ExecutionUnavailable("Container identity path contains a link")
        if container_id_file.exists() or not container_id_file.parent.is_dir():
            raise ExecutionUnavailable("Container identity file must be a new host-private file")
    name = container_name or "atomic-check-" + uuid.uuid4().hex
    if config.mode == "docker":
        engine = shutil.which("docker")
        if engine is None:
            raise ExecutionUnavailable("Docker unavailable; no local fallback. See docs/EXECUTION.md.")
        options = {} if host_spec is None else {"host_spec": host_spec, "host_spec_nonce": host_spec_nonce}
        cmd = docker_command(engine, config.image, project, name, args, profile=config.profile,
                             container_id_file=container_id_file, **options)
    else:
        cmd = [sys.executable, *_trusted_checker_args(args)]
    tails = [bytearray(), bytearray()]
    truncated = [False, False]
    collection_failed = threading.Event()

    def drain(stream, tail, index):
        try:
            read = getattr(stream, "read1", stream.read)
            while chunk := read(4096):
                if output_callback is not None:
                    output_callback(("stdout", "stderr")[index], chunk)
                tail.extend(chunk)
                if len(tail) > config.output_bytes:
                    truncated[index] = True
                    del tail[:-config.output_bytes]
        except Exception:
            # Callback exceptions may contain credential material: never preserve their message.
            collection_failed.set()
        finally:
            stream.close()

    with tempfile.TemporaryDirectory(prefix="atomic-exec-") as private_home:
        owned_job = None
        if os.name == "nt" and engine is None:
            from atomic_flow.windows_job import WindowsJob
            try:
                owned_job = WindowsJob()
            except OSError:
                raise ExecutionUnavailable("Owned Windows job unavailable; no unowned fallback") from None
        try:
            spawn = owned_job.spawn if owned_job else subprocess.Popen
            proc = spawn(cmd, cwd=project, env=clean_environment(Path(private_home)),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=os.name != "nt",
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        except BaseException:
            if owned_job:
                owned_job.close()
            raise
        readers = [threading.Thread(target=drain, args=(stream, tails[index], index), daemon=True)
                   for index, stream in enumerate((proc.stdout, proc.stderr))]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout
        timed_out = False
        try:
            while proc.poll() is None:
                check()
                if collection_failed.is_set():
                    raise ExecutionUnavailable("Output collection failed; execution stopped")
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                time.sleep(0.05)
            check()
        finally:
            # Even a successful parent must not leave background workers running.
            try:
                if owned_job:
                    try:
                        owned_job.terminate()
                    except OSError:
                        raise ExecutionUnavailable("Windows process-tree cleanup could not be confirmed; effects remain unknown") from None
                    finally:
                        if proc.poll() is None:
                            proc.kill()
                        owned_job.close()
                elif os.name != "nt" or proc.poll() is None:
                    _kill_tree(proc)
            finally:
                cleanup_confirmed = True
                if engine:
                    target = name
                    if container_id_file is not None:
                        # Never fall back to a reusable name for a durable job.
                        cid = container_id_file.read_text().strip() if container_id_file.exists() else ""
                        target = cid if re.fullmatch(r"[a-f0-9]{64}", cid) else ""
                    if target:
                        cleanup_confirmed = _remove_container(engine, target, Path(private_home))
                    elif container_id_file is not None:
                        cleanup_confirmed = False
                proc.wait(timeout=15)
                for reader in readers:
                    reader.join(timeout=2)
                if not cleanup_confirmed:
                    raise ExecutionUnavailable("Container cleanup could not be confirmed; execution effects remain unknown") from None
        if collection_failed.is_set() or any(reader.is_alive() for reader in readers):
            raise ExecutionUnavailable("Output collection failed; execution stopped")
        if owned_job and proc.returncode == 125:
            raise ExecutionUnavailable("Windows job assignment refused or workload returned reserved exit status 125")
        return CommandResult(proc.returncode, sanitize_output(tails[0].decode("utf-8", errors="replace")),
                             sanitize_output(tails[1].decode("utf-8", errors="replace")), timed_out,
                             truncated[0], truncated[1])
