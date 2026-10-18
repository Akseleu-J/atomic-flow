"""Independent cancellation signal and an OS-held single-runner task lease."""
from contextlib import contextmanager

from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.agents.task import _validate_id, tasks_dir


def request_cancel(task_id: str):
    _validate_id(task_id)
    tasks_dir().mkdir(parents=True, exist_ok=True)
    (tasks_dir() / f"{task_id}.cancel").touch()


def check_cancel(task_id: str):
    _validate_id(task_id)
    if (tasks_dir() / f"{task_id}.cancel").exists():
        raise TaskCancelled("cancelled by user")


@contextmanager
def task_lease(task_id: str):
    import os

    _validate_id(task_id)
    tasks_dir().mkdir(parents=True, exist_ok=True)
    with (tasks_dir() / f"{task_id}.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("task is already being executed by another runner") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
