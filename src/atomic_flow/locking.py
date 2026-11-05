"""Short advisory OS file leases shared by task execution and spend reservation."""
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_file(path: Path, *, blocking=False, busy_message="resource is already locked"):
    import os
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = None
    try:
        # The marker byte bootstrap races on Windows: another holder's byte-range
        # lock can deny our write before we ever request the lock. A collision
        # proves the resource is held, so blocking callers retry and non-blocking
        # callers observe the usual busy refusal instead of a raw PermissionError.
        deadline = time.monotonic() + (30 if blocking else 0)
        while True:
            handle = path.open("a+b")
            try:
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                break
            except OSError as exc:
                handle.close()
                handle = None
                now = time.monotonic()
                if not blocking or now >= deadline:
                    raise ValueError(busy_message) from exc
                time.sleep(min(0.05, max(0.0, deadline - now)))
        try:
            yield
        finally:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_UN)
            finally:
                handle.close()
                handle = None
    finally:
        if handle is not None:
            handle.close()
