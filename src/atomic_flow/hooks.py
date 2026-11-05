"""Explicit trusted-host observation hooks; callbacks never receive execution authority.

Deadlines are cooperative and checked after return. Python callbacks cannot be
forcibly preempted or sandboxed by this interface.
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any


class HookRejected(RuntimeError):
    """A before hook vetoed the proposed operation."""


class HookFailed(RuntimeError):
    """A hook failed or exceeded its cooperative deadline."""


def _freeze(value: Any):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _observation(value: dict) -> Mapping:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > 32768:
            raise ValueError("hook observation exceeds 32768 bytes")
        return _freeze(json.loads(encoded))
    except (TypeError, ValueError, RecursionError) as exc:
        raise HookFailed("hook requires bounded JSON observation") from exc


@dataclass(frozen=True)
class _Registration:
    callback: Callable
    timeout_seconds: float


class HookManager:
    """Host-selected callbacks. Only ``before_tool`` may return False to veto."""

    EVENTS = frozenset({"before_tool", "after_tool", "run_start", "run_end", "run_error"})

    def __init__(self):
        self._hooks: dict[str, list[_Registration]] = {}
        self._lock = threading.RLock()

    def register(self, event: str, callback: Callable, *, approved: bool = False,
                 timeout_seconds: float = 1.0) -> None:
        if not approved:
            raise PermissionError("host hook registration requires explicit approval")
        if (event not in self.EVENTS or not callable(callback) or isinstance(timeout_seconds, bool)
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30):
            raise ValueError("invalid hook registration")
        with self._lock:
            if sum(map(len, self._hooks.values())) >= 32:
                raise ValueError("hook registration limit reached")
            self._hooks.setdefault(event, []).append(_Registration(callback, timeout_seconds))

    def lifecycle(self, event: str, observation: dict, *, cancel_check: Callable = lambda: None) -> None:
        if event not in self.EVENTS:
            raise ValueError("unknown hook event")
        with self._lock:
            hooks = tuple(self._hooks.get(event, ()))
        if not hooks:
            return
        data = _observation({**observation, "event": event})
        for hook in hooks:
            cancel_check()
            started = time.monotonic()
            try:
                response = hook.callback(data)
            except Exception as exc:
                # Callback errors can contain credentials; keep the public error bounded and generic.
                raise HookFailed(f"{event} callback failed") from exc
            cancel_check()
            if time.monotonic() - started >= hook.timeout_seconds:
                raise HookFailed(f"{event} cooperative deadline exceeded")
            if event == "before_tool" and response is False:
                raise HookRejected("before_tool hook vetoed operation")
            if response is not None and not (event == "before_tool" and response is True):
                raise HookFailed("hook may return only None or a before_tool boolean veto")

    def before_tool(self, name: str, args: dict, contract, role: str = "", *, cancel_check: Callable = lambda: None) -> None:
        self.lifecycle("before_tool", {"tool": name, "args": args, "contract": asdict(contract), "role": role},
                       cancel_check=cancel_check)

    def after_tool(self, name: str, args: dict, contract, result: dict, role: str = "", *,
                   cancel_check: Callable = lambda: None) -> None:
        from atomic_flow.agents.result import ToolResult
        self.lifecycle("after_tool", {"tool": name, "args": args, "contract": asdict(contract),
                                      "role": role, "result": ToolResult.from_payload(result).summary()},
                       cancel_check=cancel_check)
