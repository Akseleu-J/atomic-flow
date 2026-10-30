"""Pure frozen main-pair declarations; no execution, inference or qualification authority.

Caller-owned registry evidence must already have been independently accepted. Hashes
and the accepted status bind that assertion; this module cannot authenticate evidence,
attest a worker, prove a complete Git inventory or establish available provider quota.
Existing public packet projection owns the writable-path policy. No hidden corpus
bytes, arbitrary commands, runtime construction or environment access are accepted.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from atomic_flow.check_spec import CheckSpec
from tools.p07_public_task_packet import (
    SELECTION_POLICY_VERSION,
    canonical_bytes,
    packet_sha256,
    prepare_public_task_packet,
)

MODEL = "anthropic/claude-sonnet-5@default"
OWNERS = frozenset({"akseley", "umirbayulgaisha"})
ARMS = ("atomic", "flat")
MAX_TASKS = 30
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
_FACTORY_TOKEN = object()
_SHA = re.compile(r"[a-f0-9]{64}")
_COMMIT = re.compile(r"[a-f0-9]{40}")
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _exact(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(label + " requires exact fields")


def _digest(value, pattern=_SHA):
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ValueError("exact lowercase digest required")
    return value


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("bounded plain integer required")


def _usd(value):
    if (type(value) is not str or len(value) > 64
            or re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,9})?", value) is None):
        raise ValueError("USD requires a nonnegative exact decimal string")
    whole, _, fraction = value.partition(".")
    return int(whole) * 1_000_000_000 + int(fraction.ljust(9, "0"))


@dataclass(frozen=True, slots=True)
class OrdinaryAdmission:
    """Explicit host assertion, not a qualification inferred from calibration."""

    check_spec: CheckSpec
    public_packet_sha256: str
    source_manifest_sha256: str
    framework_sha: str
    workflow_sha: str
    ordinary_selection_policy_sha256: str
    runtime_admission_sha256: str
    journey_acceptance_sha256: str
    status: str

    def as_dict(self):
        if type(self.check_spec) is not CheckSpec or self.status != "accepted_full_task_journey":
            raise ValueError("explicit accepted ordinary TaskRunner qualification required")
        for name in ("public_packet_sha256", "source_manifest_sha256", "ordinary_selection_policy_sha256",
                     "runtime_admission_sha256", "journey_acceptance_sha256"):
            _digest(getattr(self, name))
        _digest(self.framework_sha, _COMMIT)
        _digest(self.workflow_sha, _COMMIT)
        return {"status": self.status, "check_spec": self.check_spec.as_dict(),
                "check_spec_sha256": self.check_spec.digest,
                **{name: getattr(self, name) for name in ("public_packet_sha256", "source_manifest_sha256",
                   "framework_sha", "workflow_sha", "ordinary_selection_policy_sha256",
                   "runtime_admission_sha256", "journey_acceptance_sha256")}}


def _profile(value):
    _exact(value, ("campaign_id", "framework_sha", "workflow_sha", "corpus_sha256", "dataset_revision",
                   "source_profile_sha256", "ordinary_selection_policy_sha256", "wire_config", "limits",
                   "total_budget_usd", "owner_caps", "arm_budget_usd", "task_budget_usd",
                   "reserve_per_call_usd", "deadline_unix_ms", "prior_spend_usd", "candidate_selection"), "profile")
    if type(value["campaign_id"]) is not str or _ID.fullmatch(value["campaign_id"]) is None:
        raise ValueError("plain campaign identity required")
    for name in ("framework_sha", "workflow_sha", "dataset_revision"):
        _digest(value[name], _COMMIT)
    for name in ("corpus_sha256", "source_profile_sha256", "ordinary_selection_policy_sha256"):
        _digest(value[name])
    if value["candidate_selection"] != "last_retained_candidate_at_stop_v1":
        raise ValueError("fixed no-hidden-feedback candidate rule required")
    wire = value["wire_config"]
    _exact(wire, ("model", "max_input_tokens", "max_output_tokens", "temperature", "max_retries"), "wire_config")
    if wire["model"] != MODEL or type(wire["max_retries"]) is not int or wire["max_retries"] != 0:
        raise ValueError("fixed Sonnet model and zero transport retries required")
    _integer(wire["max_input_tokens"], 1, 10_000_000)
    _integer(wire["max_output_tokens"], 16, 200_000)
    temperature = wire["temperature"]
    if temperature is not None and (type(temperature) is not str
            or re.fullmatch(r"(?:(?:0|1)(?:\.[0-9]+)?|2(?:\.0+)?)", temperature) is None):
        raise ValueError("exact queue temperature required")
    limits = value["limits"]
    bounds = {"max_model_calls": (1, 1000), "max_duration_seconds": (1, 604800),
              "max_replans": (0, 5), "max_attempts": (1, 10), "max_request_bytes": (4096, 2_000_000),
              "context_budget_tokens": (256, 200_000)}
    _exact(limits, bounds, "common arm limits")
    for name, (minimum, maximum) in bounds.items():
        _integer(limits[name], minimum, maximum)
    _integer(value["deadline_unix_ms"], 1, 9_007_199_254_740_991)
    total, arm, task, reserve = (_usd(value[name]) for name in
                                ("total_budget_usd", "arm_budget_usd", "task_budget_usd", "reserve_per_call_usd"))
    owners = value["owner_caps"]
    if type(owners) is not dict or not owners or not set(owners) <= OWNERS:
        raise ValueError("explicit fixed authorized owner caps required")
    if not 0 < reserve <= arm or task < arm * 2 or not task <= total <= 20_000_000_000:
        raise ValueError("paired arm/task/aggregate reservation ceilings differ")
    caps = {owner: _usd(cap) for owner, cap in owners.items()}
    if any(not task <= cap <= total for cap in caps.values()) or sum(caps.values()) > total:
        raise ValueError("owner allocations exceed declared authority")
    prior = value["prior_spend_usd"]
    _exact(prior, ("total", "owners"), "historic spend")
    if type(prior["owners"]) is not dict or set(prior["owners"]) != set(owners):
        raise ValueError("every fixed owner requires explicit historic spend")
    historic = _usd(prior["total"])
    if historic > total or sum(_usd(cost) for cost in prior["owners"].values()) > historic:
        raise ValueError("historic aggregate spend is inconsistent")
    if any(_usd(prior["owners"][owner]) > cap for owner, cap in caps.items()):
        raise ValueError("historic owner spend exceeds cap")
    return packet_sha256(value)


@dataclass(frozen=True, slots=True, init=False)
class FrozenMatchedManifest:
    """Only immutable canonical bytes are retained; returned dictionaries are copies."""

    canonical: bytes

    def __init__(self, canonical, *, _factory_token=None):
        if _factory_token is not _FACTORY_TOKEN or type(canonical) is not bytes:
            raise ValueError("use freeze_matched_manifest for validated immutable bytes")
        object.__setattr__(self, "canonical", canonical)

    @property
    def sha256(self):
        return hashlib.sha256(self.canonical).hexdigest()

    def as_dict(self):
        return json.loads(self.canonical)


def freeze_matched_manifest(*, profile, expected_task_ids, packets, ordinary_registry, schedule):
    """Declare exactly one Atomic and flat arm per explicitly expected task.

    This is a main/pilot manifest for the five registered public repository families,
    not a holdout selector or ablation schema. Qualification hashes are host inputs.
    Both arms reference one shared public packet, ordinary admission and profile;
    per-arm budget/scope/runtime overrides are structurally unavailable.
    """
    profile_sha = _profile(profile)
    if (type(expected_task_ids) is not tuple or not 1 <= len(expected_task_ids) <= MAX_TASKS
            or any(type(task) is not str or _ID.fullmatch(task) is None for task in expected_task_ids)
            or len(set(expected_task_ids)) != len(expected_task_ids)):
        raise ValueError("bounded unique explicit expected task identities required")
    expected = set(expected_task_ids)
    if type(packets) is not dict or set(packets) != expected:
        raise ValueError("complete exact public packet cohort required")
    if type(ordinary_registry) is not dict or set(ordinary_registry) != expected:
        raise ValueError("complete exact qualified ordinary registry required")
    if type(schedule) is not tuple or len(schedule) != len(expected):
        raise ValueError("one ordered schedule row per task required")
    rows, seen, owner_tasks = [], set(), {owner: 0 for owner in profile["owner_caps"]}
    for position, item in enumerate(schedule):
        _exact(item, ("task_id", "owner", "arm_order"), "schedule row")
        task_id, owner, order = item["task_id"], item["owner"], item["arm_order"]
        if type(task_id) is not str or task_id not in expected or task_id in seen:
            raise ValueError("schedule task missing, repeated or outside frozen cohort")
        if type(owner) is not str or owner not in owner_tasks:
            raise ValueError("task requires one named authorized owner")
        if (type(order) is not tuple or len(order) != 2
                or any(type(arm) is not str for arm in order) or set(order) != set(ARMS)):
            raise ValueError("each task requires exactly one Atomic and flat arm")
        # Counterbalance by declared schedule order, independently of result/owner.
        if order != (ARMS if position % 2 == 0 else ARMS[::-1]):
            raise ValueError("predeclared alternating paired arm order required")
        packet = packets[task_id]
        if type(packet) is not dict:
            raise ValueError("exact public packet required")
        try:
            rebuilt = prepare_public_task_packet(
                packet["task"], packet["source_manifest"], framework_sha=profile["framework_sha"],
                workflow_sha=profile["workflow_sha"], source_profile_sha256=profile["source_profile_sha256"],
                protected_paths=tuple(packet["protected_paths"]))
        except (KeyError, TypeError):
            raise ValueError("complete canonical public packet required") from None
        if packet != rebuilt or set(packet["task"]) != {"instance_id", "repo", "base_commit", "problem_statement"}:
            raise ValueError("public packet changed, narrowed or contains unapproved hints")
        if packet["task"]["instance_id"] != task_id:
            raise ValueError("public task identity differs from schedule")
        admission = ordinary_registry[task_id]
        if type(admission) is not OrdinaryAdmission:
            raise ValueError("explicit qualified ordinary registry entry required")
        admitted = admission.as_dict()
        spec = admission.check_spec
        if (admission.public_packet_sha256 != packet_sha256(packet)
                or admission.source_manifest_sha256 != packet["source_manifest_sha256"]
                or admission.framework_sha != profile["framework_sha"]
                or admission.workflow_sha != profile["workflow_sha"]
                or admission.ordinary_selection_policy_sha256 != profile["ordinary_selection_policy_sha256"]
                or spec.task_id != task_id or spec.base_commit != packet["task"]["base_commit"]):
            raise ValueError("ordinary qualification differs from exact task/source/runtime/profile")
        manifest, writable = packet["source_manifest"], set(packet["writable_paths"])
        if any(path not in manifest or path in writable for path in spec.test_paths):
            raise ValueError("ordinary test files must exist and be immutable in public base")
        common = {"public_packet": packet, "public_packet_sha256": packet_sha256(packet),
                  "ordinary_admission": admitted, "ordinary_admission_sha256": packet_sha256(admitted),
                  "profile_sha256": profile_sha}
        rows.append({"task_id": task_id, "owner": owner, "common": common,
                     "arms": [{"arm": arm, "profile_sha256": profile_sha,
                               "public_packet_sha256": common["public_packet_sha256"],
                               "ordinary_admission_sha256": common["ordinary_admission_sha256"]} for arm in order]})
        seen.add(task_id)
        owner_tasks[owner] += 1
    if seen != expected:
        raise ValueError("scheduled cohort is incomplete")
    arm = _usd(profile["arm_budget_usd"])
    prior = profile["prior_spend_usd"]
    for owner, count in owner_tasks.items():
        if count * 2 * arm + _usd(prior["owners"][owner]) > _usd(profile["owner_caps"][owner]):
            raise ValueError("declared paired cohort exceeds fixed owner allocation")
    if len(rows) * 2 * arm + _usd(prior["total"]) > _usd(profile["total_budget_usd"]):
        raise ValueError("declared cohort plus historic spend exceeds aggregate ceiling")
    value = {"schema_version": 1, "kind": "p07_frozen_matched_main_manifest",
             "authority": "declaration_only_requires_separate_host_admission",
             "selection_policy": SELECTION_POLICY_VERSION, "profile": profile,
             "profile_sha256": profile_sha, "expected_task_ids": list(expected_task_ids), "pairs": rows}
    raw = canonical_bytes(value)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("matched manifest exceeds fixed byte bound")
    return FrozenMatchedManifest(raw, _factory_token=_FACTORY_TOKEN)


def candidate_identity(manifest, task_id, arm, *, candidate_sha256):
    """Bind one retained candidate's bytes; this is neither a grade nor completion."""
    if type(manifest) is not FrozenMatchedManifest or arm not in ARMS:
        raise ValueError("frozen manifest and registered arm required")
    _digest(candidate_sha256)
    value = manifest.as_dict()
    pair = next((row for row in value["pairs"] if row["task_id"] == task_id), None)
    if pair is None:
        raise ValueError("candidate task outside frozen cohort")
    return {"schema_version": 1, "kind": "p07_matched_candidate_identity",
            "manifest_sha256": manifest.sha256, "profile_sha256": value["profile_sha256"],
            "task_id": task_id, "arm": arm, "owner": pair["owner"],
            "public_packet_sha256": pair["common"]["public_packet_sha256"],
            "ordinary_admission_sha256": pair["common"]["ordinary_admission_sha256"],
            "candidate_sha256": candidate_sha256}


def validate_candidate_identity(manifest, value):
    """Refuse swaps across cohort, owner, arm, profile, source and candidate bytes."""
    if type(value) is not dict:
        raise ValueError("exact candidate identity required")
    try:
        expected = candidate_identity(manifest, value["task_id"], value["arm"], candidate_sha256=value["candidate_sha256"])
    except KeyError:
        raise ValueError("complete candidate identity required") from None
    if value != expected:
        raise ValueError("candidate identity differs from frozen manifest")
    return dict(expected)
