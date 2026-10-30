"""Inspect a concrete matched runner without granting admission or executing it.

This probe is a future driver building block, not a wrapper around run_plan,
an approval, a source attestation, an accounting lock or permission to send.
It takes concrete TaskRunner, GitHubQueueBackend and CheckObligation objects
and checks selected fields without locking any mutable object or source tree.
"""
from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path

from atomic_flow.agents.envelope import runtime_digest
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.backends.github_queue import GitHubQueueBackend
from atomic_flow.check_obligation import CheckObligation
from tools.p07_flat_task_runner import FlatTaskRunner
from tools.p07_matched_arm_contract import arm_projection, validate_arm_runtime
from tools.p07_public_task_packet import packet_sha256


def inspect_runner_snapshot(manifest, task_id, arm, runner):
    """Inspect actual bound objects; return hashes, never permission to execute.

    The source lease and public Git object audit, durable cross-arm paid ledger,
    exact queue destination approval, policy/role/callback and resource-limit
    equivalence, and runner review are separate mandatory gates. In particular,
    ``runtime_digest`` does not bind ``runner.resource_limits``. A snapshot does
    not eliminate TOCTOU or establish actual enforcement during execution.
    """
    projection = arm_projection(manifest, task_id, arm)
    expected_class = TaskRunner if arm == "atomic" else FlatTaskRunner
    if type(runner) is not expected_class:
        raise ValueError("concrete reviewed runner class differs from arm")
    backend, obligation, config, project = runner.backend, runner.check_obligation, runner.config, runner.project
    if type(backend) is not GitHubQueueBackend or type(obligation) is not CheckObligation:
        raise ValueError("exact queue backend and host obligation objects required")
    if (not isinstance(runner.project, Path) or runner.capability_factory is not None
            or runner.config.routing.mode != "fixed" or runner.config.execution.profile != "checks"):
        raise ValueError("built-in capabilities and fixed Docker checker required")
    # The queue owns this check and refuses its config, identity or destination drift.
    backend._verify_binding()
    selected = validate_arm_runtime(manifest, task_id, arm, config=config,
                                    backend_config=backend.config, queue_identity=backend.identity,
                                    check_spec=obligation.template)
    public = projection["public_packet"]
    protected = dict(obligation.protected_files)
    for name in public["protected_paths"]:
        if name not in public["source_manifest"] or protected.get(name) != public["source_manifest"][name]:
            raise ValueError("obligation does not bind public protected file bytes")
    # The task's test gate cannot extend the separately declared campaign deadline.
    deadline_ms = Decimal(str(obligation.deadline_at)) * 1000
    if deadline_ms > Decimal(projection["deadline_unix_ms"]) or obligation.deadline_at <= time.time():
        raise ValueError("obligation deadline exceeds campaign or is already expired")
    digest = runtime_digest(config, project, backend, check_obligation=obligation)
    # Recheck selected fields and digest to catch ordinary drift; this is not a lock.
    backend._verify_binding()
    if (runner.backend is not backend or runner.check_obligation is not obligation
            or runner.config is not config or runner.project != project or runner.capability_factory is not None
            or config.routing.mode != "fixed" or config.execution.profile != "checks"):
        raise ValueError("runner objects changed during snapshot")
    selected_again = validate_arm_runtime(manifest, task_id, arm, config=config,
                                         backend_config=backend.config, queue_identity=backend.identity,
                                         check_spec=obligation.template)
    if (selected_again != selected or runtime_digest(config, project, backend, check_obligation=obligation) != digest
            or obligation.deadline_at <= time.time()):
        raise ValueError("runner binding changed during snapshot")
    backend._verify_binding()
    return {"scope": "live_objects_snapshot_only_not_execution_authority",
            "selected_fields": selected,
            "runtime_digest": digest, "obligation_digest": obligation.digest,
            "queue_destination_sha256": packet_sha256(backend.transport_identity),
            "limits": "no source attestation, no live ledger reservation, no queue destination approval, "
                      "no callback/resource-limit equivalence, no lock"}
