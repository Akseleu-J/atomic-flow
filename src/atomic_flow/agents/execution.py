"""Bounded command execution. Local mode is explicitly NOT a security sandbox."""
from __future__ import annotations

import os
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

from atomic_flow.config import ExecutionConfig


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


def docker_command(engine: str, image: str, workspace: Path, name: str, args: list[str]) -> list[str]:
    return [engine, "run", "--name", name, "--rm", "--pull=never", "--network=none",
            "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--pids-limit=128", "--memory=1g", "--cpus=2", f"--user={container_identity()}",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=128m,mode=1777", "--workdir=/workspace",
            "--mount", f"type=bind,src={workspace.resolve()},dst=/workspace",
            "--env=HOME=/tmp", "--env=PYTHONDONTWRITEBYTECODE=1",
            "--env=PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "--", image, "python", *args]


def _kill_tree(proc: subprocess.Popen):
    if os.name == "nt":
        # Best effort on Windows; a detached child whose parent already exited can escape.
        subprocess.run([str(Path(os.environ["SYSTEMROOT"]) / "System32" / "taskkill.exe"),
                        "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=10)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()


def run_python(args: list[str], project: Path, *, config: ExecutionConfig | None = None,
               timeout: float = 120, cancel_check: Callable[[], None] | None = None) -> CommandResult:
    """Execute Python arguments; docker mode fails closed without an engine/image.

    Cancel checks raise TaskCancelled. Output retains only a bounded tail per stream.
    Local mode reduces accidental credential inheritance but permits host file/network access.
    """
    config = config or ExecutionConfig()
    check = cancel_check or (lambda: None)
    check()
    engine = None
    name = "atomic-check-" + uuid.uuid4().hex
    if config.mode == "docker":
        engine = shutil.which("docker")
        if engine is None:
            raise ExecutionUnavailable("Docker unavailable; no local fallback. See docs/EXECUTION.md.")
        cmd = docker_command(engine, config.image, project, name, args)
    else:
        cmd = [sys.executable, *args]
    tails = [bytearray(), bytearray()]

    def drain(stream, tail):
        try:
            while chunk := stream.read(4096):
                tail.extend(chunk)
                if len(tail) > config.output_bytes:
                    del tail[:-config.output_bytes]
        finally:
            stream.close()

    with tempfile.TemporaryDirectory(prefix="atomic-exec-") as private_home:
        proc = subprocess.Popen(cmd, cwd=project, env=clean_environment(Path(private_home)),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=os.name != "nt",
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        readers = [threading.Thread(target=drain, args=(stream, tail), daemon=True)
                   for stream, tail in zip((proc.stdout, proc.stderr), tails, strict=True)]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout
        timed_out = False
        try:
            while proc.poll() is None:
                check()
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
                if engine:
                    subprocess.run([engine, "rm", "-f", name], capture_output=True, timeout=15,
                                   env=clean_environment(Path(private_home)))
                proc.wait(timeout=15)
                for reader in readers:
                    reader.join(timeout=2)
        return CommandResult(proc.returncode, tails[0].decode("utf-8", errors="replace"),
                             tails[1].decode("utf-8", errors="replace"), timed_out)
