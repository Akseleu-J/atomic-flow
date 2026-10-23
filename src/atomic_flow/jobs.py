"""Host-owned durable Python jobs. Source snapshots are staging, not a sandbox.

Docker is the default execution boundary. Explicit local mode is for trusted code
only. A lost local worker leaves effects unknown; reconciliation never kills a PID
read from disk and never replays work.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from atomic_flow.agents.execution import _remove_container, clean_environment
from atomic_flow.agents.source import SourceConflict, confined_path
from atomic_flow.agents.tools.file_paths import admitted_path
from atomic_flow.config import ExecutionConfig, user_dir
from atomic_flow.locking import exclusive_file
from atomic_flow.tree.defaults import DEFAULT_IGNORE

TERMINAL = frozenset({"succeeded", "failed", "cancelled", "timed_out", "output_limit", "interrupted"})
_ID = re.compile(r"job_[a-f0-9]{24}\Z")
_MAX_STATE = 1024 * 1024


class JobError(ValueError):
    """Invalid job, unavailable output, or a failed ownership/integrity gate."""


def _safe(path: Path, *, regular=False):
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise JobError("job paths may not contain links or junctions")
    if regular and path.exists() and not stat.S_ISREG(path.stat().st_mode):
        raise JobError("job storage requires regular files")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _snapshot_hashes(root: Path, excluded: Path | None = None, *, private_workspace: bool | None = None):
    import fnmatch
    result, total = {}, 0
    ctx = SimpleNamespace(project_root=root, private_workspace=(excluded is None if private_workspace is None else private_workspace),
                          tree_ignore=[], respect_gitignore=True)
    for current, dirs, files in os.walk(root, followlinks=False):
        folder = Path(current)
        dirs[:] = [name for name in dirs if not any(fnmatch.fnmatch(name, pattern) for pattern in DEFAULT_IGNORE)
                   and (not name.startswith(".") or name == ".github")
                   and (excluded is None or (folder / name).resolve() != excluded)]
        for name in dirs:
            confined_path(root, (folder / name).relative_to(root).as_posix())
        for name in sorted(files):
            rel = (folder / name).relative_to(root).as_posix()
            try:
                path = admitted_path(ctx, rel, existing=True)
            except SourceConflict:
                continue
            total += path.stat().st_size
            if len(result) >= 10000 or total > 100 * 1024 * 1024:
                raise JobError("source snapshot exceeds file/byte limits")
            result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _atomic(path: Path, value):
    _safe(path, regular=True)
    data = _json(value)
    if len(data) > _MAX_STATE:
        raise JobError("job metadata exceeds storage limit")
    temporary = path.with_name(".state-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as handle:
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class JobStore:
    """Project-scoped host API; no credentials or model budgets are accepted.

    Output retains a bounded prefix, with byte cursors and explicit incomplete
    markers. Reaching the combined stdout/stderr cap requests process termination.
    A terminal result is not source publication or task approval.
    """

    def __init__(self, project: Path, root: Path | None = None, *, private_workspace: bool = False):
        self.project = Path(project).expanduser().resolve(strict=True)
        if not self.project.is_dir():
            raise JobError("job project must be an existing directory")
        if type(private_workspace) is not bool:
            raise JobError("private workspace admission must be explicit host boolean")
        self.private_workspace = private_workspace
        if self.project.is_relative_to(user_dir().resolve()) and not private_workspace:
            raise JobError("private application state is not a source project")
        allowed = user_dir().expanduser().absolute() / "jobs"
        self.root = Path(root).expanduser().absolute() if root else allowed
        _safe(self.root)
        _safe(allowed)
        if (not self.root.resolve().is_relative_to(allowed.resolve()) or self.root.resolve().is_relative_to(self.project)
                or len(self.root.resolve().relative_to(allowed.resolve()).parts) > 1
                or (self.root != allowed and (self.root.name.startswith("project_")
                                             or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", self.root.name) is None))):
            raise JobError("job storage must stay under ATOMIC_FLOW_HOME/jobs outside source project")
        self.admission_root = allowed
        self.project_id = hashlib.sha256(os.path.normcase(str(self.project)).encode()).hexdigest()
        self.directory = self.root / ("project_" + self.project_id)

    def _directory(self, job_id):
        if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
            raise JobError("invalid job ID")
        directory = self.directory / job_id
        _safe(directory)
        _safe(directory / "workspace")
        _safe(directory / "host_home")
        for name in ("state.json", "state.lock", "worker.lock", "cancel", "stdout.bin", "stderr.bin", "container.id"):
            _safe(directory / name, regular=True)
        return directory

    def _read(self, job_id):
        path = self._directory(job_id) / "state.json"
        try:
            with path.open("rb") as handle:
                data = handle.read(_MAX_STATE + 1)
            if len(data) > _MAX_STATE:
                raise JobError("job metadata exceeds storage limit")
            state = json.loads(data)
            if (state.get("schema_version") != 1 or type(state["schema_version"]) is not int
                    or state.get("id") != job_id or state.get("project_id") != self.project_id
                    or state.get("project") != str(self.project)
                    or state.get("status") not in TERMINAL | {"preparing", "queued", "running"}
                    or state.get("container_name") != "atomic-job-" + job_id[4:]
                    or not isinstance(state.get("source_hashes"), dict)
                    or not isinstance(state.get("cursor_key"), str) or len(bytes.fromhex(state["cursor_key"])) != 32):
                raise JobError("job schema or project identity mismatch")
            if (len(state["source_hashes"]) > 10000
                    or any(type(state.get(field)) is not bool for field in ("effects_unknown", "output_complete", "output_expired"))
                    or any(type(state.get(field)) not in (int, float) or not math.isfinite(state[field])
                           for field in ("created_at", "updated_at"))
                    or (state["status"] in TERMINAL and (type(state.get("finished_at")) not in (int, float)
                                                         or not math.isfinite(state["finished_at"])))
                    or type(state.get("discarded_bytes")) is not int or state["discarded_bytes"] < 0):
                raise JobError("invalid job lifecycle metadata")
            if state.get("snapshot_hash") != hashlib.sha256(_json(state["source_hashes"])).hexdigest():
                raise JobError("source snapshot manifest digest mismatch")
            ExecutionConfig.model_validate(state["execution"])
            if (type(state["output_bytes"]) is not int or not 1024 <= state["output_bytes"] <= 16 * 1024 * 1024
                    or not math.isfinite(state["timeout"]) or not 0 < state["timeout"] <= 86400
                    or not math.isfinite(state["retention_seconds"]) or not 1 <= state["retention_seconds"] <= 2592000
                    or not isinstance(state["args"], list) or not state["args"]
                    or any(not isinstance(arg, str) or "\0" in arg for arg in state["args"])
                    or len(_json(state["args"])) > 65536):
                raise JobError("invalid job execution limits")
            for rel, value in state["source_hashes"].items():
                if (not isinstance(rel, str) or not rel or "\\" in rel or ":" in rel
                        or PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts
                        or PurePosixPath(rel).as_posix() != rel or not isinstance(value, str)
                        or not re.fullmatch(r"[a-f0-9]{64}", value)):
                    raise JobError("invalid source snapshot manifest")
            return state
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            if isinstance(exc, JobError):
                raise
            raise JobError("job metadata unavailable or malformed") from None

    def _lease(self, job_id):
        directory = self._directory(job_id)
        if not directory.is_dir() or not (directory / "state.json").is_file():
            raise JobError("job not found in this project")
        return exclusive_file(directory / "state.lock", blocking=True)

    def _public(self, state):
        return {key: value for key, value in state.items() if key not in {"cursor_key", "args", "execution"}}

    def _active_count(self):
        """Only known metadata depths, never source/workspace recursive globbing."""
        roots = [self.admission_root]
        children = list(self.admission_root.iterdir())
        if len(children) > 256:
            raise JobError("global job storage directory limit exhausted")
        for child in children:
            _safe(child)
            if child.is_dir() and not child.name.startswith("project_"):
                roots.append(child)
        count, records = 0, 0
        for root in roots:
            for project in root.glob("project_*"):
                _safe(project)
                if not re.fullmatch(r"project_[a-f0-9]{64}", project.name):
                    raise JobError("invalid global job project metadata")
                for job in project.glob("job_*"):
                    _safe(job)
                    path = job / "state.json"
                    _safe(path, regular=True)
                    records += 1
                    if records > 4096:
                        raise JobError("global job metadata count exhausted")
                    try:
                        with path.open("rb") as handle:
                            data = handle.read(_MAX_STATE + 1)
                        state = json.loads(data)
                        if (len(data) > _MAX_STATE or state.get("id") != job.name
                                or not _ID.fullmatch(job.name) or state.get("schema_version") != 1
                                or "project_" + state.get("project_id", "") != project.name
                                or state.get("status") not in TERMINAL | {"preparing", "queued", "running"}):
                            raise ValueError
                    except (OSError, ValueError, TypeError, AttributeError):
                        raise JobError("global job admission metadata unavailable; reconcile before new work") from None
                    if (state["status"] not in TERMINAL
                            or (state.get("effects_unknown") and state.get("cleanup_confirmed") is not True)):
                        count += 1
        return count

    def start(self, args: list[str], *, config: ExecutionConfig | None = None, timeout: float = 120,
              output_bytes: int = 1048576, retention_seconds: float = 604800) -> dict:
        if (not isinstance(args, list) or not args or any(not isinstance(x, str) or "\0" in x for x in args)
                or len(_json(args)) > 65536 or type(output_bytes) is not int or not 1024 <= output_bytes <= 16 * 1024 * 1024
                or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 86400
                or not isinstance(retention_seconds, (int, float)) or not math.isfinite(retention_seconds)
                or not 1 <= retention_seconds <= 2592000):
            raise JobError("invalid job arguments or limits")
        config = config or ExecutionConfig()
        _safe(self.directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        job_id = "job_" + uuid.uuid4().hex[:24]
        directory = self._directory(job_id)
        now = time.time()
        state = {"schema_version": 1, "id": job_id, "project": str(self.project), "project_id": self.project_id,
                 "args": args, "execution": config.model_dump(), "timeout": float(timeout), "output_bytes": output_bytes,
                 "retention_seconds": float(retention_seconds), "status": "preparing", "created_at": now,
                 "updated_at": now, "finished_at": None, "source_hashes": {},
                 "snapshot_hash": hashlib.sha256(_json({})).hexdigest(), "cursor_key": os.urandom(32).hex(),
                 "returncode": None, "error": None, "effects_unknown": False,
                 "output_complete": False, "output_expired": False, "discarded_bytes": 0,
                 "container_name": "atomic-job-" + job_id[4:]}
        admission_lock = self.admission_root / "admission.lock"
        _safe(admission_lock, regular=True)
        with exclusive_file(admission_lock, blocking=True):
            if self._active_count() >= 4:
                raise JobError("global active job limit exhausted (4); reconcile unresolved jobs")
            if len(list(self.directory.glob("job_*"))) >= 64:
                raise JobError("job storage count exhausted (64 per project)")
            directory.mkdir(mode=0o700, parents=True)
            _atomic(directory / "state.json", state)
        # The preparing journal reserves admission and survives crashes/failures.
        # Hold the worker lease during copying so reconciliation cannot race it.
        try:
            with exclusive_file(directory / "worker.lock", busy_message="worker active"):
                snapshot = directory / "workspace"
                snapshot.mkdir()
                hashes = _snapshot_hashes(self.project, user_dir().resolve(), private_workspace=self.private_workspace)
                for rel, expected in hashes.items():
                    source = confined_path(self.project, rel)
                    target = confined_path(snapshot, rel)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                        raise JobError("source changed during snapshot")
                if _snapshot_hashes(self.project, user_dir().resolve(), private_workspace=self.private_workspace) != hashes:
                    raise JobError("source changed during snapshot")
                state.update(status="queued", source_hashes=hashes, snapshot_hash=hashlib.sha256(_json(hashes)).hexdigest(),
                             updated_at=time.time())
                with self._lease(job_id):
                    _atomic(directory / "state.json", state)
        except (OSError, ValueError):
            state.update(status="failed", error="source snapshot preparation failed", updated_at=time.time(), finished_at=time.time())
            with self._lease(job_id):
                _atomic(directory / "state.json", state)
            return self._public(state)
        private_home = directory / "host_home"
        private_home.mkdir()
        # -I and an absolute trusted package path prevent project/cwd import hijacking.
        package_root = Path(__file__).resolve().parents[1]
        bootstrap = "import sys;sys.path.insert(0,sys.argv.pop(1));from atomic_flow.job_worker import main;main()"
        command = [sys.executable, "-I", "-c", bootstrap, str(package_root), str(self.project),
                   str(self.root), job_id, str(user_dir().absolute()), str(int(self.private_workspace))]
        try:
            subprocess.Popen(command, cwd=private_home, env=clean_environment(private_home), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                             start_new_session=os.name != "nt",
                             creationflags=(subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0)
        except OSError:
            state.update(status="failed", error="worker could not be started", updated_at=time.time(), finished_at=time.time())
            _atomic(directory / "state.json", state)
        return self._public(state)

    def status(self, job_id: str) -> dict:
        with self._lease(job_id):
            state = self._read(job_id)
            if (state["status"] in TERMINAL and not state["output_expired"]
                    and time.time() - state["finished_at"] > state["retention_seconds"]):
                for stream in ("stdout", "stderr"):
                    (self._directory(job_id) / f"{stream}.bin").unlink(missing_ok=True)
                state.update(output_expired=True, output_complete=False)
                _atomic(self._directory(job_id) / "state.json", state)
            return self._public(state)

    def list(self) -> list[dict]:
        _safe(self.directory)
        return [self.status(path.name) for path in sorted(self.directory.glob("job_*")) if path.is_dir()]

    def cancel(self, job_id: str) -> dict:
        with self._lease(job_id):
            state = self._read(job_id)
            if state["status"] not in TERMINAL:
                (self._directory(job_id) / "cancel").touch(exist_ok=True)
        return self.status(job_id)

    def output(self, job_id: str, *, stream: str = "stdout", cursor: str | None = None, limit: int = 16384) -> dict:
        if stream not in {"stdout", "stderr"} or type(limit) is not int or not 1 <= limit <= 65536:
            raise JobError("invalid output stream or page limit")
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 1024):
            raise JobError("invalid output cursor")
        self.status(job_id)  # Apply retention before opening output.
        with self._lease(job_id):
            state = self._read(job_id)
            if state["output_expired"]:
                raise JobError("job output expired; full output is unavailable")
            offset = 0
            key = bytes.fromhex(state["cursor_key"])
            if cursor is not None:
                try:
                    payload64, signature = cursor.split(".")
                    payload = base64.urlsafe_b64decode(payload64 + "=" * (-len(payload64) % 4))
                    if len(cursor) > 1024 or not hmac.compare_digest(hmac.new(key, payload, hashlib.sha256).hexdigest(), signature):
                        raise ValueError
                    record = json.loads(payload)
                    if record[:3] != [self.project_id, job_id, stream] or type(record[3]) is not int or record[3] < 0:
                        raise ValueError
                    offset = record[3]
                except (ValueError, IndexError, TypeError):
                    raise JobError("invalid output cursor or stream/job scope") from None
            path = self._directory(job_id) / f"{stream}.bin"
            size = path.stat().st_size if path.exists() else 0
            if offset > size:
                raise JobError("output cursor exceeds retained output")
            with path.open("rb") if path.exists() else _Empty() as handle:
                handle.seek(offset)
                data = handle.read(limit)
            next_offset = offset + len(data)
            payload = _json([self.project_id, job_id, stream, next_offset])
            next_cursor = base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + hmac.new(key, payload, hashlib.sha256).hexdigest()
            return {"job_id": job_id, "stream": stream, "offset": offset, "next_cursor": next_cursor,
                    "data_base64": base64.b64encode(data).decode(), "text": data.decode("utf-8", errors="replace"),
                    "bytes": len(data), "retained_bytes": size, "at_end": next_offset == size,
                    "terminal": state["status"] in TERMINAL, "output_complete": state["output_complete"],
                    "discarded_bytes": state["discarded_bytes"], "encoding": "utf-8; exact bytes in data_base64"}

    def reconcile(self, job_id: str | None = None) -> list[dict]:
        ids = [job_id] if job_id else [path.name for path in self.directory.glob("job_*") if path.is_dir()]
        reconciled = []
        for identifier in ids:
            directory = self._directory(identifier)
            if not directory.is_dir() or not (directory / "state.json").is_file():
                raise JobError("job not found in this project")
            try:
                with exclusive_file(directory / "worker.lock", busy_message="worker active"):
                    with self._lease(identifier):
                        state = self._read(identifier)
                        if state["status"] in TERMINAL or (state["status"] in {"preparing", "queued"} and time.time() - state["created_at"] < 15):
                            continue
                        # No persisted PID is ever used as process ownership proof.
                        was_running = state["status"] == "running"
                        state.update(status="interrupted", finished_at=time.time(), updated_at=time.time(),
                                     effects_unknown=was_running, output_complete=False, error="worker lease lost; execution was not replayed")
                        if was_running and state["execution"]["mode"] == "docker":
                            engine = shutil.which("docker")
                            cid_path = directory / "container.id"
                            try:
                                cid = cid_path.read_text(encoding="ascii").strip() if cid_path.exists() and cid_path.stat().st_size <= 128 else ""
                            except (OSError, UnicodeError):
                                cid = ""
                            # An immutable Docker ID, written to a private host file
                            # by the owned launch, avoids deleting a reused name.
                            if engine and re.fullmatch(r"[a-f0-9]{64}", cid):
                                state["cleanup_confirmed"] = _remove_container(engine, cid, directory / "host_home")
                            else:
                                state["cleanup_confirmed"] = False
                        else:
                            state["cleanup_confirmed"] = not was_running
                        _atomic(directory / "state.json", state)
                        reconciled.append(self._public(state))
            except ValueError as exc:
                if str(exc) != "worker active":
                    raise
        return reconciled


class _Empty:
    def __enter__(self):
        import io
        self.handle = io.BytesIO()
        return self.handle

    def __exit__(self, *_):
        self.handle.close()
