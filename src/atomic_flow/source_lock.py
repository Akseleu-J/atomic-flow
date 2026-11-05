"""Root-wide advisory mutation leases for cooperating atomic-flow writers.

The lock lives in the source root so separate state directories still coordinate.
Never unlink it: deleting an open lock would create a second lock identity. External
editors do not cooperate with this protocol and are outside its exclusion contract.
"""
from __future__ import annotations

import inspect
import os
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from atomic_flow.locking import exclusive_file

LOCK_NAME = ".atomic-flow-lock"
_held = threading.local()


@contextmanager
def source_mutation_lease(project: Path, *, publication: bool = False):
    """Take the root-wide mutation lease for `project`.

    Same-thread nesting of ordinary guarded helpers (publish -> replace_checked,
    edit_node -> undo, notebooks.apply -> file_transaction.apply) is reentrant and
    allowed. `publication=True` marks a whole-source publication: entering one while
    this thread already holds the same root is refused, because a nested publication
    running between another writer's final check and its replace is exactly the
    interleaving (audit RT-01) the lease exists to exclude. Cross-thread and
    cross-process mutation is always mutually excluded at the OS level.
    """
    from atomic_flow.agents.source import SourceConflict

    root = Path(project).resolve(strict=True)
    identity = (os.getpid(), os.path.normcase(str(root)))
    owners = getattr(_held, "owners", None)
    if owners is None:
        owners = _held.owners = set()
    if identity in owners:
        if publication:
            raise SourceConflict(
                "publication cannot nest inside another guarded mutation of the same project source in this thread")
        yield
        return
    lock = root / LOCK_NAME
    if lock.is_symlink() or getattr(lock, "is_junction", lambda: False)():
        raise SourceConflict("source mutation lock cannot be a symlink/junction")
    manager = exclusive_file(lock, busy_message="project source is being mutated by another writer")
    try:
        manager.__enter__()
    except ValueError as exc:
        raise SourceConflict(str(exc)) from exc
    owners.add(identity)
    try:
        yield
    finally:
        owners.remove(identity)
        manager.__exit__(None, None, None)


def mutation_guard(argument: str, *, publication: bool = False):
    """Guard a whole operation using a named root, self root, or ToolContext."""
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def guarded(*args, **kwargs):
            bound = signature.bind(*args, **kwargs).arguments
            root = bound.get(argument)
            if root is None:
                root = getattr(bound["self"], argument)
            if argument == "ctx":
                root = root.project_root
            with source_mutation_lease(root, publication=publication):
                return function(*args, **kwargs)
        return guarded
    return decorate
