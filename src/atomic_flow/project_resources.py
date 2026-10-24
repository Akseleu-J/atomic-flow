"""Host-owned resource envelope for source traversal; no model-configured limits."""
import hashlib
import os
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from atomic_flow.errors import TreeError


class ResourceLimitError(TreeError):
    """The complete operation was refused; partial context must not be accepted."""


@dataclass(frozen=True)
class ProjectLimits:
    max_files: int = 10_000
    max_total_bytes: int = 128 * 1024 * 1024
    max_nodes: int = 100_000
    max_entries: int = 100_000
    max_depth: int = 64
    max_file_bytes: int = 16 * 1024 * 1024
    min_free_bytes: int = 64 * 1024 * 1024
    chunk_bytes: int = 256 * 1024
    # Optional host toolchain envelope, in UTF-16 code units (e.g. Windows 259).
    # Extended-path-capable hosts must not be rejected by a guessed legacy cap.
    max_workspace_path_chars: int | None = None

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for key, value in vars(self).items()
               if key != "max_workspace_path_chars"):
            raise ValueError("project resource limits must be positive integers")
        if (self.max_workspace_path_chars is not None
                and (type(self.max_workspace_path_chars) is not int or self.max_workspace_path_chars < 1)):
            raise ValueError("workspace path limit must be a positive integer or None")


def require_workspace_paths(directory: Path, relative_paths, limits: ProjectLimits):
    """Refuse a known host path envelope before allocating snapshot/checkpoints.

    Include the longer checkpoint root and atomic replacement suffix. This is
    a configured interoperability envelope, not discovery of OS long-path support.
    """
    ceiling = limits.max_workspace_path_chars
    if ceiling is None:
        return
    roots = (directory / "workspace", directory / "checkpoints" / ("0" * 32))
    def admit(path):
        if len(str(path).encode("utf-16-le", errors="surrogatepass")) // 2 > ceiling:
            raise ResourceLimitError("workspace path envelope exceeded; use a shorter ATOMIC_FLOW_HOME or a reviewed toolchain path limit")

    admit(directory / ("journal.json." + "0" * 32 + ".atomic-tmp"))
    admit(directory / "git-private" / "objects" / "00" / ("0" * 38))
    for rel in relative_paths:
        for root in roots:
            target = root / rel
            admit(target.with_name(target.name + "." + "0" * 32 + ".atomic-tmp"))


def workspace_path_error(exc: OSError) -> ResourceLimitError | None:
    """Explain an actual Windows path failure without claiming generic support."""
    code = getattr(exc, "winerror", None)
    filename = getattr(exc, "filename", None)
    if code == 206 or (code == 3 and filename is not None and len(str(filename)) >= 260):
        return ResourceLimitError("workspace path unavailable on this Windows toolchain; use a shorter ATOMIC_FLOW_HOME and configure max_workspace_path_chars")
    return None


def require_disk_space(path: Path, required: int, limits: ProjectLimits):
    """Admission estimate, not a reservation against concurrent disk users."""
    probe = path
    while not probe.exists():
        probe = probe.parent
    if shutil.disk_usage(probe).free < required + limits.min_free_bytes:
        raise ResourceLimitError("workspace disk reserve would be exhausted")


def stream_source(path: Path, limits: ProjectLimits, cancel_check, *, sink=None,
                  remaining_bytes: int | None = None) -> tuple[str, int]:
    """Hash/copy one regular file in bounded chunks and reject changed reads."""
    fingerprint = hashlib.sha1()
    size = 0
    cancel_check()
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ResourceLimitError("snapshot source must be a regular file")
        if before.st_size > limits.max_file_bytes:
            raise ResourceLimitError("project per-file byte limit exceeded")
        ceiling = limits.max_file_bytes if remaining_bytes is None else min(limits.max_file_bytes, remaining_bytes)
        if before.st_size > ceiling:
            raise ResourceLimitError("project total byte limit exceeded")
        while block := handle.read(min(limits.chunk_bytes, ceiling - size + 1)):
            cancel_check()
            size += len(block)
            if size > limits.max_file_bytes:
                raise ResourceLimitError("project per-file byte limit exceeded")
            if size > ceiling:
                raise ResourceLimitError("project total byte limit exceeded")
            fingerprint.update(block)
            if sink is not None:
                sink.write(block)
        after = os.fstat(handle.fileno())
    # Include inode/device so replacing a path during a read is not accepted as
    # the bytes of the new file. Hash comparisons still provide final freshness.
    def identity(item):
        return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns
    if size != before.st_size or identity(before) != identity(after) or identity(after) != identity(path.stat()):
        raise ResourceLimitError("project source changed during bounded read")
    cancel_check()
    return fingerprint.hexdigest(), size


def source_files(root: Path, skip, limits: ProjectLimits, cancel_check: Callable[[], None], *, private_root=None):
    """Incremental scandir admission, without os.walk's unbounded directory list."""
    stack, entries = [(root, 0)], 0
    while stack:
        cancel_check()
        directory, depth = stack.pop()
        with __import__("os").scandir(directory) as iterator:
            admitted = []
            for entry in iterator:
                cancel_check()
                entries += 1
                if entries > limits.max_entries:
                    raise ResourceLimitError("project traversal entry limit exceeded")
                path = Path(entry.path)
                rel = path.relative_to(root).as_posix()
                is_dir = entry.is_dir(follow_symlinks=False)
                if skip(rel, is_dir) or entry.is_symlink():
                    continue
                if private_root is not None and path.resolve() == private_root:
                    continue
                if is_dir:
                    if depth >= limits.max_depth:
                        raise ResourceLimitError("project traversal depth limit exceeded")
                    stack.append((path, depth + 1))
                elif entry.is_file(follow_symlinks=False):
                    admitted.append((rel, path))
            yield from sorted(admitted)
