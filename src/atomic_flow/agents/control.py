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
    from atomic_flow.locking import exclusive_file

    _validate_id(task_id)
    with exclusive_file(tasks_dir() / f"{task_id}.lock",
                        busy_message="task is already being executed by another runner"):
        yield
