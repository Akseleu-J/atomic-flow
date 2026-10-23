"""Bounded command execution. Local mode is explicitly NOT a security sandbox."""
from __future__ import annotations

import os
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
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from atomic_flow.config import ExecutionConfig
from atomic_flow.output import sanitize_output


class ExecutionUnavailable(RuntimeError):
    pass


class TaskCancelled(RuntimeError):
    pass


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
import importlib, os, runpy, sys
tool, arguments = sys.argv[1], sys.argv[2:]
if tool == 'pytest':
    import pytest
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
                   profile: str = "checks", container_id_file: Path | None = None) -> list[str]:
    bounds = execution_profile(profile)
    root = str(workspace.resolve())
    if any(character in root for character in (",", "\n", "\r", "\0")):
        raise ExecutionUnavailable("Workspace path cannot be represented safely as a Docker bind mount")
    mount = f"type=bind,src={root},dst=/workspace" + (",readonly" if bounds.workspace_read_only else "")
    cid_args = ["--cidfile", str(container_id_file.absolute())] if container_id_file is not None else []
    return [engine, "run", *cid_args, "--name", name, "--rm", "--pull=never", "--network=none",
            "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            f"--pids-limit={bounds.pids}", f"--memory={bounds.memory}", f"--cpus={bounds.cpus}",
            f"--user={container_identity()}",
            f"--tmpfs=/tmp:rw,noexec,nosuid,size={bounds.tmpfs_size},mode=1777", "--workdir=/workspace",
            "--mount", mount,
            "--env=HOME=/tmp", "--env=PYTHONDONTWRITEBYTECODE=1",
            "--env=PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "--", image, "python", *_trusted_checker_args(args)]


def _kill_tree(proc: subprocess.Popen):
    uncertain = False
    try:
        if os.name == "nt":
            # Best effort; detached children can escape. Do not let a failed helper
            # skip termination of the Popen we actually own, or retain helper pipes.
            try:
                subprocess.run([str(Path(os.environ["SYSTEMROOT"]) / "System32" / "taskkill.exe"),
                                "/PID", str(proc.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                uncertain = True
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
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
               container_name: str | None = None, container_id_file: Path | None = None) -> CommandResult:
    """Execute Python arguments; docker mode fails closed without an engine/image.

    Cancel checks raise TaskCancelled. Output retains only a bounded tail per stream.
    Local mode reduces accidental credential inheritance but permits host file/network access.
    """
    config = config or ExecutionConfig()
    execution_profile(config.profile)
    if config.mode == "local" and config.profile != "checks":
        raise ExecutionUnavailable("The selected execution profile requires Docker; local mode is unsandboxed")
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
        cmd = docker_command(engine, config.image, project, name, args, profile=config.profile, container_id_file=container_id_file)
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
        proc = subprocess.Popen(cmd, cwd=project, env=clean_environment(Path(private_home)),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=os.name != "nt",
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
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
                if os.name != "nt" or proc.poll() is None:
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
        return CommandResult(proc.returncode, sanitize_output(tails[0].decode("utf-8", errors="replace")),
                             sanitize_output(tails[1].decode("utf-8", errors="replace")), timed_out,
                             truncated[0], truncated[1])
