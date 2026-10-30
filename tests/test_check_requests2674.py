"""Requests2674 fixed runtime, public CA and same-worker fixture contract."""
import hashlib
from dataclasses import replace

import pytest
import test_check_requests_execution_overlay as core
from test_check_private_pytest import bound as private_bound

from atomic_flow.check_fixture import FixtureIdentity, reviewed_helper_inventory
from atomic_flow.check_requests2674 import POLICY, function_args, function_receipt, health_bytes
from atomic_flow.check_requests_execution_overlay import prepare_requests_execution_overlay
from atomic_flow.check_requests_resources import RequestsCAIdentity


def test_2674_resource_fixture_source_is_bound_and_isolated(tmp_path):
    candidate, execution, original = core.bound.__wrapped__(tmp_path)
    private_spec, identity = private_bound((2, 7, 18))
    runtime = private_spec.runtime_identity
    helpers = reviewed_helper_inventory("2674-v2")
    def sha(raw):
        return hashlib.sha256(raw).hexdigest()
    fixture = FixtureIdentity(identity.task_id, identity.variant, runtime.digest, runtime.image_id,
        "61ed4c2119f1b93719297b54277f61293410b769ef5c090db7010177aa56a289", helpers,
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
    assert "import base64,errno,socket,ssl,threading,urllib2" in source
    assert "ssl.PROTOCOL_TLSv1_2" in source and "urllib2.ProxyHandler({})" in source
    assert source.index("function_fetch('https://httpbin.org/") < source.index("sys.path.insert(0,root)")
    assert "finally:\n    function_stop()" in source
    from test_check_requests_function import packet, raw_observation

    value = raw_observation(execution_spec, overlay)
    value["requests_function_fixture"]["policy"] = POLICY
    value["requests_function_fixture"]["health"]["mapped_http_alias"] = {"host": "www.google.co.uk", "status": 200}
    assert function_receipt(packet(value), execution_spec, "7" * 32) == value
    from atomic_flow.check_execution import CheckReceipt, completed_check_receipt
    receipt = CheckReceipt(execution_spec.digest, execution_spec.source_revision, execution_spec.source_revision, True, value, packet(value))
    assert completed_check_receipt(execution_spec, receipt)
    value["requests_function_fixture"]["health"]["wrong_ca"] = "accepted"
    assert function_receipt(packet(value), execution_spec, "7" * 32) is None
    with pytest.raises(ValueError):
        function_args(replace(execution_spec, source_revision="f" * 64), "7" * 32, overlay)
