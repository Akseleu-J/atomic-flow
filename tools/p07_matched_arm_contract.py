"""Bind one arm's declared queue and runtime limits to a frozen matched manifest.

This pure check neither builds a runner nor authenticates source, evidence or
provider authority. The future one-arm driver must run it on the actual bound
objects and separately enforce the host lease, clock, budget and final gate.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from atomic_flow.check_spec import CheckSpec
from atomic_flow.config import AtomicConfig, BackendConfig
from tools.p07_matched_manifest import ARMS, FrozenMatchedManifest
from tools.p07_public_task_packet import packet_sha256


def arm_projection(manifest: FrozenMatchedManifest, task_id: str, arm: str) -> dict:
    """Return a host-only arm declaration derived solely from the frozen cohort."""
    if type(manifest) is not FrozenMatchedManifest or type(task_id) is not str or arm not in ARMS:
        raise ValueError("frozen manifest and exact registered arm required")
    value = manifest.as_dict()
    pair = next((pair for pair in value["pairs"] if pair["task_id"] == task_id), None)
    if pair is None:
        raise ValueError("task outside frozen cohort")
    profile = value["profile"]
    order = [entry["arm"] for entry in pair["arms"]]
    identity = {"campaign_id": profile["campaign_id"], "owner": pair["owner"], "arm": arm,
                "task_id": task_id, "framework_sha": profile["framework_sha"],
                "workflow_sha": profile["workflow_sha"], "profile_sha256": value["profile_sha256"]}
    return {"schema_version": 1, "scope": "host_declaration_only_not_execution_authority",
            "manifest_sha256": manifest.sha256, "task_id": task_id, "arm": arm,
            "owner": pair["owner"], "order_index": order.index(arm), "queue_identity": identity,
            "profile_sha256": value["profile_sha256"], "wire_config": profile["wire_config"],
            "limits": profile["limits"], "arm_budget_usd": profile["arm_budget_usd"],
            "task_budget_usd": profile["task_budget_usd"], "reserve_per_call_usd": profile["reserve_per_call_usd"],
            "deadline_unix_ms": profile["deadline_unix_ms"], "candidate_selection": profile["candidate_selection"],
            "public_packet": pair["common"]["public_packet"],
            "public_packet_sha256": pair["common"]["public_packet_sha256"],
            "ordinary_admission": pair["common"]["ordinary_admission"],
            "ordinary_admission_sha256": pair["common"]["ordinary_admission_sha256"]}


def validate_arm_runtime(manifest: FrozenMatchedManifest, task_id: str, arm: str, *, config: AtomicConfig,
                         backend_config: BackendConfig, queue_identity: dict, check_spec: CheckSpec) -> dict:
    """Refuse common cap, queue identity, source scope or checker substitutions.

    Checks a supplied snapshot. It does not instantiate TaskRunner, prove a
    GitHub queue destination, freeze mutable objects, check a live deadline,
    validate tariffs, or prove host/worker admission. The caller must retain
    its normal runtime digest and durable broker reservation gate.
    """
    projection = arm_projection(manifest, task_id, arm)
    if type(config) is not AtomicConfig or type(backend_config) is not BackendConfig or type(check_spec) is not CheckSpec:
        raise ValueError("typed runtime, queue config and ordinary CheckSpec required")
    if type(queue_identity) is not dict or queue_identity != projection["queue_identity"]:
        raise ValueError("queue identity differs from frozen arm")
    admitted = projection["ordinary_admission"]
    if check_spec.as_dict() != admitted["check_spec"] or check_spec.digest != admitted["check_spec_sha256"]:
        raise ValueError("ordinary CheckSpec differs from frozen arm")
    wire = projection["wire_config"]
    if (type(backend_config.max_retries) is not int or type(backend_config.max_input_tokens) is not int
            or type(backend_config.max_output_tokens) is not int):
        raise ValueError("queue token and retry limits require exact integers")
    observed_wire = {"model": backend_config.model, "max_input_tokens": backend_config.max_input_tokens,
                     "max_output_tokens": backend_config.max_output_tokens,
                     "temperature": None if backend_config.temperature is None else str(backend_config.temperature),
                     "max_retries": backend_config.max_retries}
    if (observed_wire != wire or backend_config.tool_protocol != "text_json"
            or backend_config.api_key or backend_config.api_key_env or backend_config.base_url):
        raise ValueError("queue wire config differs from frozen arm")
    limits = projection["limits"]
    if (any(type(value) is not int for value in (config.task.max_model_calls, config.task.max_replans,
                                                  config.max_attempts, config.conversation.max_request_bytes,
                                                  config.context_budget_tokens))
            or type(config.task.max_duration_seconds) not in (int, float)):
        raise ValueError("runtime limit types differ from frozen arm")
    observed_limits = {"max_model_calls": config.task.max_model_calls,
                       "max_duration_seconds": config.task.max_duration_seconds,
                       "max_replans": config.task.max_replans, "max_attempts": config.max_attempts,
                       "max_request_bytes": config.conversation.max_request_bytes,
                       "context_budget_tokens": config.context_budget_tokens}
    if observed_limits != limits:
        raise ValueError("common runtime limits differ from frozen arm")
    try:
        budget = Decimal(str(config.task.max_budget_usd))
        expected_budget = Decimal(projection["arm_budget_usd"])
    except InvalidOperation:
        raise ValueError("invalid runtime budget") from None
    if not budget.is_finite() or budget != expected_budget or config.task.fail_on_budget_exceeded is not True:
        raise ValueError("runtime arm budget differs from frozen arm")
    expected_strategy = None if arm == "atomic" else "flat-baseline-v1"
    if (config.task.execution_strategy != expected_strategy or config.task.final_check != "ordinary_test"
            or config.execution.mode != "docker" or config.execution.image != check_spec.image):
        raise ValueError("runtime strategy or host checker differs from frozen arm")
    return {"scope": "selected_runtime_fields_matched_no_admission", "arm_projection_sha256": packet_sha256(projection),
            "manifest_sha256": manifest.sha256, "task_id": task_id, "arm": arm,
            "queue_identity_sha256": packet_sha256(queue_identity), "check_spec_sha256": check_spec.digest}
