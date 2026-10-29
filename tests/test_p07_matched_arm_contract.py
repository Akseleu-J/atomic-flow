"""Frozen matched-arm runtime checks do not grant paid or execution authority."""
from copy import deepcopy
from dataclasses import replace

import pytest
from test_p07_matched_manifest import IDS, inputs

from atomic_flow.config import AtomicConfig, BackendConfig
from tools.p07_matched_arm_contract import arm_projection, validate_arm_runtime
from tools.p07_matched_manifest import freeze_matched_manifest


def declared(arm="atomic"):
    args = inputs()
    manifest = freeze_matched_manifest(**args)
    profile = args["profile"]
    limits = profile["limits"]
    config = AtomicConfig()
    config.task.execution_strategy = None if arm == "atomic" else "flat-baseline-v1"
    config.task.max_model_calls = limits["max_model_calls"]
    config.task.max_duration_seconds = limits["max_duration_seconds"]
    config.task.max_replans = limits["max_replans"]
    config.task.max_budget_usd = float(profile["arm_budget_usd"])
    config.task.fail_on_budget_exceeded = True
    config.task.final_check = "ordinary_test"
    config.execution.mode = "docker"
    config.execution.image = args["ordinary_registry"][IDS[0]].check_spec.image
    config.max_attempts = limits["max_attempts"]
    config.context_budget_tokens = limits["context_budget_tokens"]
    config.conversation.max_request_bytes = limits["max_request_bytes"]
    wire = profile["wire_config"]
    backend = BackendConfig(model=wire["model"], max_input_tokens=wire["max_input_tokens"],
                            max_output_tokens=wire["max_output_tokens"], max_retries=0, temperature=None)
    projection = arm_projection(manifest, IDS[0], arm)
    return manifest, config, backend, projection["queue_identity"], args["ordinary_registry"][IDS[0]].check_spec


def call(arm, values):
    manifest, config, backend, identity, spec = values
    return validate_arm_runtime(manifest, IDS[0], arm, config=config, backend_config=backend,
                                queue_identity=identity, check_spec=spec)


def test_both_arms_get_identical_common_envelope_and_distinct_queue_identity():
    manifest, *_ = declared()
    atomic = arm_projection(manifest, IDS[0], "atomic")
    flat = arm_projection(manifest, IDS[0], "flat")
    for key in ("wire_config", "limits", "arm_budget_usd", "task_budget_usd", "reserve_per_call_usd",
                "deadline_unix_ms", "public_packet", "ordinary_admission", "profile_sha256"):
        assert atomic[key] == flat[key]
    assert (atomic["order_index"], flat["order_index"]) == (0, 1)
    assert atomic["queue_identity"] != flat["queue_identity"]
    assert call("atomic", declared())["scope"] == "selected_runtime_fields_matched_no_admission"
    assert call("flat", declared("flat"))["arm"] == "flat"


@pytest.mark.parametrize("field,value", [
    ("max_model_calls", 11), ("max_duration_seconds", 901), ("max_replans", 1),
    ("max_budget_usd", 0.23), ("fail_on_budget_exceeded", False),
    ("final_check", "syntax"), ("execution_strategy", "flat-baseline-v1"),
])
def test_runtime_task_substitution_is_refused(field, value):
    values = declared()
    setattr(values[1].task, field, value)
    with pytest.raises(ValueError):
        call("atomic", values)


@pytest.mark.parametrize("field,value", [
    ("model", "other"), ("max_retries", 1), ("max_input_tokens", 8191),
    ("max_output_tokens", 1025), ("temperature", 0.1), ("api_key", "untrusted"),
    ("api_key_env", "UNTRUSTED"), ("base_url", "https://example.invalid"), ("tool_protocol", "native"),
])
def test_queue_wire_or_credential_substitution_is_refused(field, value):
    values = declared()
    setattr(values[2], field, value)
    with pytest.raises(ValueError):
        call("atomic", values)


def test_queue_owner_or_arm_and_ordinary_spec_swaps_are_refused():
    values = declared()
    wrong_identity = {**values[3], "owner": "umirbayulgaisha"}
    with pytest.raises(ValueError, match="queue identity"):
        call("atomic", (*values[:3], wrong_identity, values[4]))
    with pytest.raises(ValueError, match="queue identity"):
        call("atomic", (*values[:3], {**values[3], "arm": "flat"}, values[4]))
    with pytest.raises(ValueError, match="CheckSpec"):
        call("atomic", (*values[:4], replace(values[4], source_revision="9" * 64)))


def test_boolean_or_float_masquerading_as_integer_limit_is_refused():
    values = declared()
    values[2].max_retries = False
    with pytest.raises(ValueError, match="exact integers"):
        call("atomic", values)
    values = declared()
    values[1].task.max_model_calls = 10.0
    with pytest.raises(ValueError, match="types"):
        call("atomic", values)


def test_execution_image_mode_and_other_shared_limits_are_refused():
    for mutate in (lambda config: setattr(config.execution, "mode", "local"),
                   lambda config: setattr(config.execution, "image", "bad"),
                   lambda config: setattr(config, "context_budget_tokens", 9000),
                   lambda config: setattr(config, "max_attempts", 4),
                   lambda config: setattr(config.conversation, "max_request_bytes", 131073)):
        values = declared()
        mutate(values[1])
        with pytest.raises(ValueError):
            call("atomic", values)


def test_projection_is_a_copy_and_extra_identity_fields_are_refused():
    values = declared()
    manifest = values[0]
    projected = arm_projection(manifest, IDS[0], "atomic")
    changed = deepcopy(projected)
    changed["public_packet"]["writable_paths"].clear()
    assert arm_projection(manifest, IDS[0], "atomic") == projected
    with pytest.raises(ValueError, match="queue identity"):
        call("atomic", (*values[:3], {**values[3], "extra": 1}, values[4]))
    with pytest.raises(ValueError):
        arm_projection(manifest, IDS[0], "missing")
