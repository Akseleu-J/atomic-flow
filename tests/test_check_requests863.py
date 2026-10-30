"""Requests863 fixed runtime, public CA and same-worker fixture contract."""
import ast
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
import test_check_requests_execution_overlay as core
from test_check_private_pytest import bound as private_bound

from atomic_flow.check_fixture import FixtureIdentity, reviewed_helper_inventory
from atomic_flow.check_requests863 import POLICY, function_args, function_receipt, health_bytes
from atomic_flow.check_requests_execution_overlay import prepare_requests_execution_overlay
from atomic_flow.check_requests_resources import RequestsCAIdentity


def test_863_resource_fixture_source_is_bound_and_isolated(tmp_path, monkeypatch):
    candidate, execution, original = core.bound.__wrapped__(tmp_path)
    private_spec, identity = private_bound((2, 7, 13))
    runtime = private_spec.runtime_identity
    helpers = reviewed_helper_inventory("863-v7")
    def sha(raw):
        return hashlib.sha256(raw).hexdigest()
    fixture = FixtureIdentity(identity.task_id, identity.variant, runtime.digest, runtime.image_id,
        "93b6a88a99cf04c62984c2404af94bf647c139f4f9f294576ea7b3505126f31c", helpers,
        (("/opt/p07-function-fixture/cacert.pem", sha(core.PUBLIC_CA)),
         ("/opt/p07-function-fixture/cert.pem", "4" * 64)), "5" * 64, "6" * 64)
    ca = RequestsCAIdentity(identity.task_id, sha(core.ORIGINAL_CA), sha(core.PUBLIC_CA),
                            sha(core.ORIGINAL_CA + b"\n" + core.PUBLIC_CA),
                            "-----BEGIN CERTIFICATE-----\nunrelated synthetic public fixture\n-----END CERTIFICATE-----\n")
    fixture = replace(fixture, health_policy_sha256=sha(health_bytes(fixture, ca)))
    spec = replace(original, task_id=identity.task_id, image=runtime.image_id, interpreter=runtime.interpreter,
                   runtime_identity=runtime, private_pytest_identity=identity, fixture_identity=fixture, requests_ca_identity=ca)
    execution_spec, overlay = prepare_requests_execution_overlay(spec, candidate, execution, core.PUBLIC_CA,
                                                                 original_ca_sha256=sha(core.ORIGINAL_CA))
    source = function_args(execution_spec, "7" * 32, overlay)[1]
    parsed = ast.parse(source, feature_version=(3, 6))
    child = next(node.value.func.value.value for node in ast.walk(parsed)
                 if isinstance(node, ast.Assign) and any(isinstance(x, ast.Name) and x.id == "child" for x in node.targets)
                 and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute))
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    from p07_requests863_service import CHILD
    assert child == CHILD
    assert "import base64,errno,socket,ssl,threading,urllib,urllib2" in source
    assert "ssl.PROTOCOL_SSLv3" in source and "urllib2.ProxyHandler({})" in source
    assert source.index("verified_chain_and_hostname") < source.index("sys.path.insert(0,root)")
    assert "finally:\n    stop_function_servers()" in source
    from test_check_requests_function import packet, raw_observation

    value = raw_observation(execution_spec, overlay)
    observed = value["requests_function_fixture"]
    observed["policy"] = POLICY
    observed["health"] = {
        "http": {"status": 200, "args": {"health": "p07"}},
        "https": {"status": 200, "args": {"health": "p07"}},
        "basic_auth": {"status": 200, "authenticated": True, "user": "p07"},
        "wrong_ca": "certificate_verification_failed", "port1": "connection_refused",
        "github_cookie": {"status": 200, "cookie": "p07_fixture=public; Path=/; HttpOnly"},
        "http11_keepalive_close": "real_persistent_connection", "chunked_request": "abc",
        "legacy_sslv3": "verified_chain_and_hostname", "wrong_hostname": "certificate_verification_failed",
        "digest": "real_response_hash_validated_wrong_password_rejected",
        "chunked_head_redirect": "real_302_chunked_header_no_HEAD_body",
    }
    assert function_receipt(packet(value), execution_spec, "7" * 32) == value
    from atomic_flow.check_execution import CheckReceipt, completed_check_receipt
    receipt = CheckReceipt(execution_spec.digest, execution_spec.source_revision, execution_spec.source_revision, True, value, packet(value))
    assert completed_check_receipt(execution_spec, receipt)
    observed["health"]["legacy_sslv3"] = "not_verified"
    assert function_receipt(packet(value), execution_spec, "7" * 32) is None
    with pytest.raises(ValueError):
        function_args(replace(execution_spec, source_revision="f" * 64), "7" * 32, overlay)
