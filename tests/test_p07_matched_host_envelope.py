"""Frozen caller declarations are not authority or proof of live host state."""
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest
from test_p07_matched_manifest import IDS, inputs

from atomic_flow.project_resources import ProjectLimits
from tools.p07_matched_host_envelope import FrozenHostEnvelope, freeze_host_envelope, validate_host_declaration
from tools.p07_matched_manifest import freeze_matched_manifest


def declarations():
    return {
        "callbacks": {"approval": {"mode": "absent"}, "question": {"mode": "absent"},
                      "cancel": {"mode": "registered", "implementation_sha256": "a" * 64,
                                 "parameters_sha256": "b" * 64}},
        "queue": {"repo": "example/private", "branch": "codex/p07-model-queue", "prefix": "queue",
                  "implementation_sha256": "c" * 64, "journal_sha256": "d" * 64,
                  "checkpoint_sha256": "e" * 64, "poll_ms": 2000, "wait_ms": 60000},
    }


def freeze(*, limits=None, data=None):
    manifest = freeze_matched_manifest(**inputs())
    values = declarations() if data is None else data
    return freeze_host_envelope(manifest, IDS[0], resource_limits=limits, **values)


def test_exact_common_declaration_is_immutable_and_not_admission():
    value = declarations()
    envelope = freeze(data=value)
    decoded = envelope.as_dict()
    assert decoded["scope"] == "caller_declarations_only_no_admission"
    assert validate_host_declaration(envelope, decoded) == {
        "scope": "declaration_equality_only_no_admission", "envelope_sha256": envelope.sha256}
    value["queue"]["prefix"] = "changed"
    decoded["queue"]["prefix"] = "changed-again"
    assert envelope.as_dict()["queue"]["prefix"] == "queue"
    with pytest.raises(FrozenInstanceError):
        envelope.canonical = b"{}"
    with pytest.raises(ValueError, match="use freeze"):
        FrozenHostEnvelope(b"{}")


@pytest.mark.parametrize("change", ["resource", "callback", "transport", "journal", "checkpoint", "timing", "bool", "extra"])
def test_any_changed_common_declaration_fails_exact_canonical_comparison(change):
    envelope = freeze()
    candidate = envelope.as_dict()
    if change == "resource":
        candidate["resource_limits"]["effective"]["max_files"] = 5
    elif change == "callback":
        candidate["callbacks"]["approval"] = {"mode": "registered", "implementation_sha256": "a" * 64,
                                               "parameters_sha256": "b" * 64}
    elif change == "transport":
        candidate["queue"]["implementation_sha256"] = "f" * 64
    elif change == "journal":
        candidate["queue"]["journal_sha256"] = "f" * 64
    elif change == "checkpoint":
        candidate["queue"]["checkpoint_sha256"] = "f" * 64
    elif change == "timing":
        candidate["queue"]["wait_ms"] += 1
    elif change == "bool":
        candidate["resource_limits"]["effective"]["max_files"] = True
    else:
        candidate["unexpected"] = 1
    with pytest.raises(ValueError, match="differs"):
        validate_host_declaration(envelope, candidate)


def test_default_and_explicit_limit_resolution_is_recorded():
    automatic = freeze()
    explicit = freeze(limits=ProjectLimits())
    assert automatic.as_dict()["resource_limits"]["effective"] == explicit.as_dict()["resource_limits"]["effective"]
    assert automatic.sha256 != explicit.sha256
    with pytest.raises(ValueError, match="differs"):
        validate_host_declaration(automatic, explicit.as_dict())


@pytest.mark.parametrize("mutation", ["callback", "timing", "boolean", "journal", "path", "unknown"])
def test_malformed_declarations_are_rejected(mutation):
    value = declarations()
    if mutation == "callback":
        value["callbacks"]["approval"] = {"mode": "registered", "implementation_sha256": "a" * 64}
    elif mutation == "timing":
        value["queue"]["wait_ms"] = 600001
    elif mutation == "boolean":
        value["queue"]["poll_ms"] = True
    elif mutation == "journal":
        value["queue"]["journal_sha256"] = "a" * 63
    elif mutation == "path":
        value["queue"]["branch"] = "../untrusted"
    else:
        value["queue"]["surprise"] = 1
    with pytest.raises(ValueError):
        freeze(data=value)


def test_changed_frozen_resource_object_is_revalidated():
    limits = ProjectLimits()
    object.__setattr__(limits, "max_files", 0)
    with pytest.raises(ValueError):
        freeze(limits=limits)


def test_no_callables_or_non_json_candidate_objects_are_admitted():
    value = declarations()
    value["callbacks"]["approval"] = lambda: True
    with pytest.raises(ValueError):
        freeze(data=value)
    envelope = freeze()
    candidate = deepcopy(envelope.as_dict())
    candidate["queue"]["poll_ms"] = float("nan")
    with pytest.raises(ValueError, match="canonical JSON"):
        validate_host_declaration(envelope, candidate)
