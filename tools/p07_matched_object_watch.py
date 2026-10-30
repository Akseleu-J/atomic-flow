"""Observe selected object bindings between two host-controlled instants.

Not a lock, admission, effect guard, provider authorization or source audit.
No check here invokes callbacks, queue I/O, model inference or runner methods.
The caller must arrange a separate authorized effect boundary and call ``check``
there; a successful check alone never establishes safety of a later operation.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path

from atomic_flow.project_resources import ProjectLimits
from tools.p07_matched_runner_probe import inspect_runner_snapshot

_TOKEN = object()


def _resources(value):
    if value is None:
        return {"resolution": "default", "effective": asdict(ProjectLimits())}
    if type(value) is not ProjectLimits:
        raise ValueError("exact ProjectLimits or default required")
    # Revalidate frozen dataclasses changed through object.__setattr__.
    return {"resolution": "explicit", "effective": asdict(ProjectLimits(**asdict(value)))}


def _queue_state(backend):
    if not isinstance(backend.root, Path) or not backend.root.is_absolute():
        raise ValueError("absolute queue journal root required")
    for value, upper in ((backend.poll_seconds, 30), (backend.wait_seconds, 600)):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= upper:
            raise ValueError("bounded queue timing required")
    return (str(backend.root), backend.poll_seconds, backend.wait_seconds)


@dataclass(frozen=True, slots=True, init=False)
class ObjectWatch:
    """In-process references and selected values, never an authority token."""
    _manifest: object
    _task_id: str
    _arm: str
    _runner: object
    _backend: object
    _runner_config: object
    _obligation: object
    _transport: object
    _backend_config: object
    _identity: object
    _transport_identity: object
    _callbacks: tuple
    _resource_object: object
    _resource_values: dict
    _queue_values: tuple
    _baseline: dict

    def __init__(self, *, token, manifest, task_id, arm, runner, baseline):
        if token is not _TOKEN:
            raise ValueError("use watch_runner to capture a reviewed snapshot")
        backend = runner.backend
        values = {"_manifest": manifest, "_task_id": task_id, "_arm": arm, "_runner": runner,
                  "_backend": backend, "_runner_config": runner.config, "_obligation": runner.check_obligation,
                  "_transport": backend.transport, "_backend_config": backend.config,
                  "_identity": backend.identity, "_transport_identity": backend.transport_identity,
                  "_callbacks": (runner.approval_callback, runner.question_callback, runner.capability_factory,
                                 backend.cancel_check),
                  "_resource_object": runner.resource_limits, "_resource_values": _resources(runner.resource_limits),
                  "_queue_values": _queue_state(backend), "_baseline": baseline}
        for key, value in values.items():
            object.__setattr__(self, key, value)

    def check(self):
        """Compare watched values now; result has no authority over an effect."""
        runner, backend = self._runner, self._backend
        if (runner.backend is not backend or runner.config is not self._runner_config
                or runner.check_obligation is not self._obligation or backend.transport is not self._transport
                or backend.config is not self._backend_config
                or backend.identity is not self._identity or backend.transport_identity is not self._transport_identity
                or runner.resource_limits is not self._resource_object
                or runner.approval_callback is not self._callbacks[0]
                or runner.question_callback is not self._callbacks[1]
                or runner.capability_factory is not self._callbacks[2]
                or backend.cancel_check is not self._callbacks[3]):
            raise ValueError("watched object reference changed")
        if _resources(runner.resource_limits) != self._resource_values or _queue_state(backend) != self._queue_values:
            raise ValueError("watched host value changed")
        current = inspect_runner_snapshot(self._manifest, self._task_id, self._arm, runner)
        if current != self._baseline:
            raise ValueError("selected runtime snapshot changed")
        # Check references and values again around the snapshot. This catches
        # ordinary concurrent drift, not an adversarial mutate-and-restore race.
        if (runner.backend is not backend or runner.config is not self._runner_config
                or runner.check_obligation is not self._obligation or backend.transport is not self._transport
                or backend.config is not self._backend_config or backend.identity is not self._identity
                or backend.transport_identity is not self._transport_identity or runner.resource_limits is not self._resource_object
                or runner.approval_callback is not self._callbacks[0] or runner.question_callback is not self._callbacks[1]
                or runner.capability_factory is not self._callbacks[2] or backend.cancel_check is not self._callbacks[3]
                or _resources(runner.resource_limits) != self._resource_values or _queue_state(backend) != self._queue_values):
            raise ValueError("watched state changed during observation")
        return {"scope": "selected_in_process_observation_only_no_authority",
                "runtime_digest": current["runtime_digest"], "task_id": self._task_id, "arm": self._arm}


def watch_runner(manifest, task_id, arm, runner):
    """Capture references after selected runtime inspection, then recheck them."""
    baseline = inspect_runner_snapshot(manifest, task_id, arm, runner)
    watch = ObjectWatch(token=_TOKEN, manifest=manifest, task_id=task_id, arm=arm, runner=runner, baseline=baseline)
    watch.check()
    return watch
