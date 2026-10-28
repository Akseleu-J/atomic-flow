"""Pure matched declarations: no targets, credentials, Docker, network or subprocesses."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import pytest

from atomic_flow.check_spec import CheckSpec
from tools.p07_matched_manifest import (
    FrozenMatchedManifest,
    OrdinaryAdmission,
    candidate_identity,
    freeze_matched_manifest,
    validate_candidate_identity,
)
from tools.p07_public_task_packet import packet_sha256, prepare_public_task_packet

IDS = ("sympy__sympy-20212", "sympy__sympy-21614")


def inputs():
    profile = {
        "campaign_id": "p07_main_declaration_test", "framework_sha": "b" * 40, "workflow_sha": "c" * 40,
        "corpus_sha256": "1" * 64, "dataset_revision": "a" * 40,
        "source_profile_sha256": "d" * 64, "ordinary_selection_policy_sha256": "2" * 64,
        "wire_config": {"model": "anthropic/claude-sonnet-5@default", "max_input_tokens": 8192,
                        "max_output_tokens": 1024, "temperature": None, "max_retries": 0},
        "limits": {"max_model_calls": 10, "max_duration_seconds": 900, "max_replans": 2,
                   "max_attempts": 3, "max_request_bytes": 131072, "context_budget_tokens": 8000},
        "total_budget_usd": "20", "owner_caps": {"akseley": "9.5", "umirbayulgaisha": "10"},
        "arm_budget_usd": "0.22", "task_budget_usd": "0.44", "reserve_per_call_usd": "0.01",
        "deadline_unix_ms": 1_900_000_000_000,
        "prior_spend_usd": {"total": "0.035931375", "owners": {"akseley": "0", "umirbayulgaisha": "0"}},
        "candidate_selection": "last_retained_candidate_at_stop_v1",
    }
    packets, registry = {}, {}
    for task_id in IDS:
        task = {"instance_id": task_id, "repo": "sympy/sympy", "base_commit": "a" * 40,
                "problem_statement": "Public bug report; no private solution data."}
        manifest = {"sympy/a.py": "e" * 64, "sympy/b.py": "f" * 64,
                    "sympy/tests/test_public.py": "3" * 64, "README.md": "4" * 64}
        packet = prepare_public_task_packet(task, manifest, framework_sha=profile["framework_sha"],
                                            workflow_sha=profile["workflow_sha"],
                                            source_profile_sha256=profile["source_profile_sha256"],
                                            protected_paths=("sympy/tests/test_public.py",))
        spec = CheckSpec(task_id=task_id, task_commit="a" * 40, base_commit="a" * 40,
                         source_revision="5" * 64, image="python@sha256:" + "6" * 64,
                         interpreter="/usr/local/bin/python", runner="pytest",
                         test_paths=("sympy/tests/test_public.py",))
        packets[task_id] = packet
        registry[task_id] = OrdinaryAdmission(
            check_spec=spec, public_packet_sha256=packet_sha256(packet),
            source_manifest_sha256=packet["source_manifest_sha256"], framework_sha=profile["framework_sha"],
            workflow_sha=profile["workflow_sha"], ordinary_selection_policy_sha256=profile["ordinary_selection_policy_sha256"],
            runtime_admission_sha256="7" * 64, journey_acceptance_sha256="8" * 64,
            status="accepted_full_task_journey")
    schedule = ({"task_id": IDS[0], "owner": "akseley", "arm_order": ("atomic", "flat")},
                {"task_id": IDS[1], "owner": "umirbayulgaisha", "arm_order": ("flat", "atomic")})
    return dict(profile=profile, expected_task_ids=IDS, packets=packets,
                ordinary_registry=registry, schedule=schedule)


def test_both_arms_share_complete_public_scope_profile_and_qualified_ordinary_spec():
    args = inputs()
    frozen = freeze_matched_manifest(**args)
    result = frozen.as_dict()
    assert result["authority"] == "declaration_only_requires_separate_host_admission"
    assert len(result["pairs"]) == 2
    assert result["profile_sha256"] == packet_sha256(args["profile"])
    for pair in result["pairs"]:
        assert {arm["arm"] for arm in pair["arms"]} == {"atomic", "flat"}
        assert pair["common"]["public_packet"]["writable_paths"] == ["sympy/a.py", "sympy/b.py"]
        assert "sympy/tests/test_public.py" in pair["common"]["public_packet"]["source_manifest"]
        assert len({arm["profile_sha256"] for arm in pair["arms"]}) == 1
        assert len({arm["public_packet_sha256"] for arm in pair["arms"]}) == 1
        assert len({arm["ordinary_admission_sha256"] for arm in pair["arms"]}) == 1


def test_freeze_retains_bytes_not_mutable_input_or_returned_dict_references():
    args = inputs()
    frozen = freeze_matched_manifest(**args)
    digest, raw = frozen.sha256, frozen.canonical
    args["profile"]["arm_budget_usd"] = "100"
    args["packets"][IDS[0]]["writable_paths"].clear()
    copy = frozen.as_dict()
    copy["pairs"].clear()
    assert frozen.sha256 == digest and frozen.canonical == raw
    assert len(frozen.as_dict()["pairs"]) == 2
    with pytest.raises(FrozenInstanceError):
        frozen.canonical = b"changed"
    with pytest.raises(ValueError):
        FrozenMatchedManifest(b'{}')


@pytest.mark.parametrize("field,value", [
    ("framework_sha", "main"), ("workflow_sha", "A" * 40), ("dataset_revision", "branch"),
    ("corpus_sha256", "9" * 63), ("source_profile_sha256", "A" * 64),
    ("ordinary_selection_policy_sha256", None), ("campaign_id", "../campaign"),
    ("candidate_selection", "best_hidden_grade"), ("deadline_unix_ms", True),
    ("arm_budget_usd", 0.22), ("reserve_per_call_usd", "0"),
    ("total_budget_usd", "20.000000001"), ("task_budget_usd", "0.22"),
])
def test_rejects_unfrozen_identity_or_cap_values(field, value):
    args = inputs()
    args["profile"][field] = value
    with pytest.raises(ValueError):
        freeze_matched_manifest(**args)


@pytest.mark.parametrize("field,value", [
    ("model", "other-model"), ("max_retries", 1), ("max_retries", False),
    ("temperature", 0.5), ("temperature", "3"), ("max_input_tokens", True),
    ("max_output_tokens", 15),
])
def test_rejects_model_retry_or_wire_cap_substitution(field, value):
    args = inputs()
    args["profile"]["wire_config"][field] = value
    with pytest.raises(ValueError):
        freeze_matched_manifest(**args)


@pytest.mark.parametrize("field,value", [
    ("max_model_calls", 0), ("max_duration_seconds", 0), ("max_replans", 6),
    ("max_attempts", 0), ("max_request_bytes", 1), ("context_budget_tokens", 255),
])
def test_rejects_invalid_common_arm_limits(field, value):
    args = inputs()
    args["profile"]["limits"][field] = value
    with pytest.raises(ValueError):
        freeze_matched_manifest(**args)


@pytest.mark.parametrize("status", ["calibrated", "accepted_executor_only", "pending", "", True])
def test_calibration_or_executor_metadata_does_not_imply_full_ordinary_qualification(status):
    args = inputs()
    args["ordinary_registry"][IDS[0]] = replace(args["ordinary_registry"][IDS[0]], status=status)
    with pytest.raises(ValueError):
        freeze_matched_manifest(**args)


def test_missing_or_untyped_ordinary_entry_refuses():
    args = inputs()
    del args["ordinary_registry"][IDS[0]]
    with pytest.raises(ValueError):
        freeze_matched_manifest(**args)
    args = inputs()
    args["ordinary_registry"][IDS[0]] = {"status": "accepted_full_task_journey"}
    with pytest.raises(ValueError):
        freeze_matched_manifest(**args)


@pytest.mark.parametrize("field,value", [
    ("public_packet_sha256", "9" * 64), ("source_manifest_sha256", "9" * 64),
    ("framework_sha", "9" * 40), ("workflow_sha", "9" * 40),
    ("ordinary_selection_policy_sha256", "9" * 64), ("runtime_admission_sha256", ""),
    ("journey_acceptance_sha256", ""),
])
def test_registry_binds_exact_public_source_profile_and_runtime_evidence(field, value):
    args = inputs()
    args["ordinary_registry"][IDS[0]] = replace(args["ordinary_registry"][IDS[0]], **{field: value})
    with pytest.raises(ValueError):
        freeze_matched_manifest(**args)


def test_ordinary_spec_cannot_be_swapped_to_another_task_base_or_writable_file():
    for changes in ({"task_id": IDS[1]}, {"base_commit": "9" * 40}, {"test_paths": ("sympy/a.py",)},
                    {"test_paths": ("sympy/tests/test_absent.py",)}):
        args = inputs()
        entry = args["ordinary_registry"][IDS[0]]
        args["ordinary_registry"][IDS[0]] = replace(entry, check_spec=replace(entry.check_spec, **changes))
        with pytest.raises(ValueError):
            freeze_matched_manifest(**args)


def test_public_scope_cannot_be_narrowed_or_hidden_hint_added_even_with_fresh_packet_hash():
    for mutate in (lambda packet: packet["writable_paths"].pop(),
                   lambda packet: packet.update(source_path="sympy/a.py"),
                   lambda packet: packet["task"].update(hints_text="gold-derived path"),
                   lambda packet: packet["task"].update(patch="hidden patch")):
        args = inputs()
        packet = args["packets"][IDS[0]]
        mutate(packet)
        args["ordinary_registry"][IDS[0]] = replace(args["ordinary_registry"][IDS[0]], public_packet_sha256=packet_sha256(packet))
        with pytest.raises(ValueError):
            freeze_matched_manifest(**args)


def test_unique_task_cohort_and_exact_pair_arms_are_mandatory():
    for expected, schedule in (
        ((IDS[0], IDS[0]), inputs()["schedule"]),
        (IDS, (inputs()["schedule"][0], inputs()["schedule"][0])),
        (IDS, inputs()["schedule"][:1]),
        (IDS, ({"task_id": IDS[0], "owner": "akseley", "arm_order": ("atomic", "atomic")}, inputs()["schedule"][1])),
        (IDS, ({"task_id": IDS[0], "owner": "akseley", "arm_order": ("atomic",)}, inputs()["schedule"][1])),
        (IDS, ({"task_id": IDS[0], "owner": "akseley", "arm_order": ({}, "flat")}, inputs()["schedule"][1])),
    ):
        args = inputs()
        args.update(expected_task_ids=expected, schedule=schedule)
        with pytest.raises(ValueError):
            freeze_matched_manifest(**args)


def test_order_and_owner_are_frozen_without_fallback_or_per_arm_budget_overrides():
    for changes in ({"owner": "unlisted"}, {"arm_order": ("flat", "atomic")}, {"atomic_budget_usd": "1"}):
        args = inputs()
        row = {**args["schedule"][0], **changes}
        args["schedule"] = (row, args["schedule"][1])
        with pytest.raises(ValueError):
            freeze_matched_manifest(**args)


def test_owner_and_aggregate_affordability_include_both_arms_and_historic_spend():
    args = inputs()
    args["profile"]["owner_caps"]["akseley"] = "0.44"
    args["profile"]["prior_spend_usd"]["owners"]["akseley"] = "0.01"
    with pytest.raises(ValueError):
        freeze_matched_manifest(**args)
    args = inputs()
    args["profile"].update(total_budget_usd="1", owner_caps={"akseley": "0.5", "umirbayulgaisha": "0.5"})
    args["profile"]["prior_spend_usd"]["total"] = "0.2"
    with pytest.raises(ValueError):
        freeze_matched_manifest(**args)


def test_unknown_profile_or_cap_fields_cannot_create_unbound_overrides():
    for mutate in (lambda p: p.update(gold_changed_paths=["sympy/a.py"]),
                   lambda p: p["limits"].update(flat_max_model_calls=20),
                   lambda p: p["wire_config"].update(api_key="never accepted"),
                   lambda p: p["owner_caps"].update(other_owner="1")):
        args = inputs()
        mutate(args["profile"])
        with pytest.raises(ValueError):
            freeze_matched_manifest(**args)


def test_candidate_identity_binds_original_manifest_owner_task_arm_profile_and_bytes():
    frozen = freeze_matched_manifest(**inputs())
    value = candidate_identity(frozen, IDS[0], "atomic", candidate_sha256="9" * 64)
    assert validate_candidate_identity(frozen, value) == value
    assert value["candidate_sha256"] == "9" * 64
    assert "verified_success" not in value and "completed" not in value
    for field, changed in (("owner", "umirbayulgaisha"), ("manifest_sha256", "a" * 64),
                           ("public_packet_sha256", "a" * 64), ("ordinary_admission_sha256", "a" * 64),
                           ("profile_sha256", "a" * 64), ("candidate_sha256", "not-a-digest")):
        with pytest.raises(ValueError):
            validate_candidate_identity(frozen, {**value, field: changed})
    with pytest.raises(ValueError):
        candidate_identity(frozen, "unknown", "atomic", candidate_sha256="9" * 64)
    with pytest.raises(ValueError):
        candidate_identity(frozen, IDS[0], "other", candidate_sha256="9" * 64)
    with pytest.raises(ValueError):
        validate_candidate_identity(frozen, {**value, "grade": "PASS"})


def test_candidate_identity_changes_for_each_arm_and_any_registered_evidence_change():
    args = inputs()
    one = freeze_matched_manifest(**args)
    atomic = candidate_identity(one, IDS[0], "atomic", candidate_sha256="9" * 64)
    flat = candidate_identity(one, IDS[0], "flat", candidate_sha256="9" * 64)
    assert atomic != flat
    args["ordinary_registry"][IDS[0]] = replace(args["ordinary_registry"][IDS[0]], journey_acceptance_sha256="a" * 64)
    two = freeze_matched_manifest(**args)
    assert one.sha256 != two.sha256
    assert candidate_identity(two, IDS[0], "atomic", candidate_sha256="9" * 64) != atomic
    assert deepcopy(one.as_dict()) == one.as_dict()
