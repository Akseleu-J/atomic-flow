"""Pure function36 source, resource and receipt attacks; no target executes."""
import ast
import copy
import hashlib
import json
from dataclasses import replace

import pytest
import test_check_requests_execution_overlay as core_fixture
from test_check_private_pytest import NONCE, observation
from test_check_requests_execution_overlay import ORIGINAL_CA, PUBLIC_CA

from atomic_flow.agents.execution import CommandResult
from atomic_flow.check_execution import CheckReceipt, completed_check_receipt
from atomic_flow.check_requests_execution_overlay import prepare_requests_execution_overlay
from atomic_flow.check_requests_function import POLICY, function_args, function_receipt, health_bytes
from atomic_flow.check_requests_resources import RequestsCAIdentity

WRONG_CA = "-----BEGIN CERTIFICATE-----\nunrelated synthetic public fixture\n-----END CERTIFICATE-----\n"


@pytest.fixture
def bound(tmp_path):
    return core_fixture.bound.__wrapped__(tmp_path)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def seal(original):
    ca = RequestsCAIdentity(original.task_id, sha(ORIGINAL_CA), sha(PUBLIC_CA), sha(ORIGINAL_CA + b"\n" + PUBLIC_CA), WRONG_CA)
    fixture = replace(original.fixture_identity, health_policy_sha256=sha(health_bytes(original.fixture_identity, ca)))
    return replace(original, fixture_identity=fixture, requests_ca_identity=ca)


def raw_observation(spec, overlay, *, failed=False):
    value = observation(spec, spec.private_pytest_identity, failed=failed)
    checks = {scheme: {"status": 200, "args": {"health": "p07"}} for scheme in ("http", "https")}
    checks.update(basic_auth={"status": 200, "authenticated": True, "user": "p07"}, wrong_ca="certificate_verification_failed",
                  port1="connection_refused", mapped_http_alias="not_in_task_profile")
    value["requests_function_fixture"] = {"policy": POLICY, "fixture_digest": spec.fixture_identity.digest,
        "runtime_digest": spec.runtime_identity.digest, "check_spec_digest": spec.digest,
        "source_revision": spec.source_revision, "ca_identity_digest": spec.requests_ca_identity.digest,
        "execution_overlay": overlay.as_dict(), "certificate_files": dict(spec.fixture_identity.certificate_files),
        "host_mappings": dict(spec.fixture_identity.host_mappings), "ports": [80, 443], "servers_stopped": True,
        "health": checks, "health_before_target_import": True, "execution_ca_sha256": spec.requests_ca_identity.result_ca_sha256,
        "key_material_binding": "admitted_immutable_image_and_provisioning_receipt"}
    return value


def packet(value):
    return CommandResult(value["exit_code"], "ATOMIC_CHECK_SPEC_RECEIPT:" + json.dumps(value), "")


@pytest.fixture
def sealed(bound):
    candidate, execution, original = bound
    spec = seal(original)
    execution_spec, overlay = prepare_requests_execution_overlay(spec, candidate, execution, PUBLIC_CA,
                                                               original_ca_sha256=sha(ORIGINAL_CA))
    assert execution_spec == spec
    return candidate, execution, spec, overlay


@pytest.mark.parametrize("failed", [False, True])
def test_complete_pass_or_required_failure_retains_exact_execution_resources(sealed, failed):
    _, _, spec, overlay = sealed
    value = raw_observation(spec, overlay, failed=failed)
    assert function_receipt(packet(value), spec, NONCE) == value
    assert spec.requests_ca_identity.validate_bytes(ORIGINAL_CA, PUBLIC_CA) == spec.requests_ca_identity.digest
    host_receipt = CheckReceipt(spec.digest, spec.source_revision, spec.source_revision, not failed, value, packet(value))
    assert completed_check_receipt(spec, host_receipt)
    assert completed_check_receipt(spec, host_receipt, negative=True) is failed


def test_source_keeps_private_grammar_and_starts_health_before_target_import(sealed):
    _, _, spec, overlay = sealed
    source = function_args(spec, NONCE, overlay)[1]
    ast.parse(source, feature_version=(3, 6))
    assert source.index("function_fetch('https://httpbin.org/") < source.index("sys.path.insert(0,root)")
    assert "server_context.load_cert_chain(function_root+'cert.pem',function_root+'key.pem')" in source
    assert "client=ssl.create_default_context(cafile=function_execution_ca)" in source
    assert "finally:\n    function_stop()" in source


@pytest.mark.parametrize("attack", ["source", "spec", "fixture", "runtime", "ca", "overlay", "stopped", "wrong_ca",
                                   "health_before", "port", "mapped_alias", "required_skip", "teardown", "extra_field"])
def test_substitution_incomplete_health_or_required_skip_never_completes(sealed, attack):
    _, _, spec, overlay = sealed
    value = copy.deepcopy(raw_observation(spec, overlay))
    fixture = value["requests_function_fixture"]
    fields = {"source": "source_revision", "spec": "check_spec_digest", "fixture": "fixture_digest",
              "runtime": "runtime_digest", "ca": "execution_ca_sha256"}
    if attack in fields:
        fixture[fields[attack]] = "f" * 64
    elif attack == "overlay":
        fixture["execution_overlay"]["result_ca_sha256"] = "f" * 64
    elif attack == "stopped":
        fixture["servers_stopped"] = False
    elif attack == "wrong_ca":
        fixture["health"]["wrong_ca"] = "accepted"
    elif attack == "health_before":
        fixture["health_before_target_import"] = False
    elif attack == "port":
        fixture["ports"] = [8080, 8443]
    elif attack == "mapped_alias":
        fixture["host_mappings"]["external.example"] = "1.1.1.1"
    elif attack == "required_skip":
        value["skipped"] = 1
    elif attack == "teardown":
        value["teardown_failed"] = 1
    elif attack == "extra_field":
        fixture["extra"] = True
    assert function_receipt(packet(value), spec, NONCE) is None


def test_resource_identity_does_not_expand_legacy_spec_or_allow_private_key_data(bound):
    _, _, original = bound
    assert "requests_ca_identity" not in original.as_dict()
    assert replace(original, requests_ca_identity=None).canonical_bytes() == original.canonical_bytes()
    spec = seal(original)
    with pytest.raises(ValueError, match="CA identity differs"):
        replace(spec, requests_ca_identity=replace(spec.requests_ca_identity, fixture_ca_sha256="f" * 64))
    with pytest.raises(ValueError, match="public wrong CA"):
        replace(spec.requests_ca_identity, wrong_ca_pem="-----BEGIN PRIVATE KEY-----\nnot public")
    with pytest.raises(ValueError, match="differ"):
        replace(spec.requests_ca_identity, wrong_ca_pem=PUBLIC_CA.decode())
    with pytest.raises(ValueError, match="exact admitted bytes"):
        spec.requests_ca_identity.validate_bytes(ORIGINAL_CA + b"changed", PUBLIC_CA)
