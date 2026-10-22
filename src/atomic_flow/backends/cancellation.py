"""Context-local cancellation; backend signatures stay compatible with plugins."""
from contextlib import contextmanager
from contextvars import ContextVar

_check = ContextVar("atomic_transport_cancel", default=None)


@contextmanager
def cancellation_scope(check):
    token = _check.set(check)
    try:
        yield
    finally:
        _check.reset(token)


def current_check():
    return _check.get()
