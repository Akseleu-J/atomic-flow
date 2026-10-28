"""Pure Requests3362 lifecycle composition/receipt checks; no target imports."""
import ast
import copy
import json
from dataclasses import replace

import pytest

from atomic_flow.agents.execution import CommandResult
from atomic_flow.check_fixture import FixtureIdentity, PrivatePytestIdentity, reviewed_helper_inventory
from atomic_flow.check_private_pytest import MARKER
from atomic_flow.check_requests3362 import LIFECYCLE_POLICY, requests3362_args, requests3362_receipt
from atomic_flow.check_runtime import RuntimeIdentity
from atomic_flow.check_spec import CheckSpec

NONCE = "7" * 32


def spec():
    runtime = RuntimeIdentity(
        base_image="example/requests@sha256:" + "a" * 64, base_image_id="sha256:" + "b" * 64,
        image_id="sha256:" + "c" * 64, interpreter="/opt/p07-python36/bin/python3.6",
        python_version=(3, 6, 15), runner="pytest", profile_sha256="d" * 64, helper_sha256="e" * 64,
        inventory_sha256="f" * 64, provisioning_receipt_sha256="1" * 64,
        private_prefix="/opt/p07-python36", launcher="/opt/p07-runtime/python36", launcher_sha256="2" * 64)
    inventory = tuple((path, "3" * 64) for path in (
        "_pytest/__init__.py", "pytest.py", "pytest_httpbin/__init__.py", "pytest_httpbin/plugin.py", "pytest_mock.py"))
    identity = PrivatePytestIdentity("psf__requests-3362", "3362-cn", runtime.digest, (3, 6, 15),
                                     inventory, reviewed_helper_inventory("3362-cn"))
    root = identity.site_packages + "pytest_httpbin/certs/"
    fixture = FixtureIdentity(
        identity.task_id, identity.variant, runtime.digest, runtime.image_id,
        "810e98b8f663ed03c3f4b529f692fdcdcffe46364ee678ac18fcda547ad1076f",
        reviewed_helper_inventory("3362-cn"), ((root + "cacert.pem", "8" * 64), (root + "cert.pem", "9" * 64)),
        "a" * 64, "b" * 64)
    return CheckSpec(identity.task_id, "4" * 40, "5" * 40, "6" * 64, runtime.image_id, runtime.interpreter,
                     "pytest", ("tests/test_requests.py",), runtime_identity=runtime,
                     private_pytest_identity=identity, fixture_identity=fixture)


def receipt(bound, *, failed=False):
    from test_check_private_pytest import observation
    value = observation(bound, bound.private_pytest_identity, failed=failed)
    # Test helper's exact complete inventory follows the passed CheckSpec.
    value["collected"], value["completed"] = 2, 2
    value["requests3362_fixture"] = {
        "policy": LIFECYCLE_POLICY, "fixture_digest": bound.fixture_identity.digest,
        "runtime_digest": bound.runtime_identity.digest, "check_spec_digest": bound.digest,
        "source_revision": bound.source_revision, "certificate_files": dict(bound.fixture_identity.certificate_files),
        "certificate_metadata_sha256": bound.fixture_identity.certificate_metadata_sha256,
        "certificate_policy": bound.fixture_identity.certificate_policy,
        "servers": [
            {"fixture_name": "httpbin", "host": "127.0.0.1", "protocol": "http", "port": 35001, "stopped": True},
            {"fixture_name": "httpbin_secure", "host": "127.0.0.1", "protocol": "https", "port": 35002, "stopped": True},
        ],
        "server_count": 2, "servers_stopped": True, "httpbin_ca_bundle_observed": True, "ca_overlay": False,
        "key_material_binding": "separate_provisioning_evidence_required",
        "health_assurance": "ordinary_declared_tests_only_no_fabricated_health",
    }
    return value


def result(value, **changes):
    return CommandResult(**({"returncode": value["exit_code"], "stdout": MARKER + json.dumps(value), "stderr": ""} | changes))


def test_pure_source_composition_has_private_grammar_and_exact_fixture_bytes_before_tests():
    bound = spec()
    args = requests3362_args(bound, NONCE)
    assert args[0] == "-c" and len(args) == 2
    source = args[1]
    ast.parse(source, feature_version=(3, 6))
    assert "ssl._ssl._test_decode_cert(server_certificate)" in source
    assert "common_names!=['127.0.0.1'] or decoded.get('subjectAltName')" in source
    assert "checked_file(absolute,expected,16384)" in source
    assert source.index("certificate_hashes={}") < source.index("import pytest,_pytest,pkg_resources")
    assert "key.pem" not in source and "PRIVATE KEY" not in source
    assert "append_fixture_ca" not in source and "requests/cacert.pem" not in source
    assert "PAYLOAD_JSON" not in source and "FIXTURE_JSON" not in source
    assert source.count("code=int(pytest.main(arguments,plugins=[evidence]))") == 1


def test_captures_actual_plugin_fixtures_before_teardown_and_stops_even_when_pytest_raises():
    source = requests3362_args(spec(), NONCE)[1]
    assert "@pytest.mark.tryfirst\n    def pytest_runtest_teardown" in source
    assert "server=item.funcargs.get(name)" in source
    assert "'httpbin_ca_bundle' in item.funcargs" in source
    assert "os.environ.get('REQUESTS_CA_BUNDLE')!=fixture['certificate_root']+'cacert.pem'" in source
    assert "try:\n    code=int(pytest.main(arguments,plugins=[evidence]))\nfinally:\n    fixture_stop()" in source
    assert "threading.Thread.join(server,3)" in source
    assert "server._server.server_close()" in source
    assert "server._server.shutdown()" in source
    assert "not server.is_alive()" in source
    assert "len(fixture_servers)<2" in source
    assert source.index("fixture_stop()\nif (") < source.index("receipt={'nonce':")
    assert "health_assurance" in source and "no_fabricated_health" in source


@pytest.mark.parametrize("failed", [False, True])
def test_complete_pass_or_assertion_failure_requires_actual_stopped_http_and_tls_fixtures(failed):
    bound = spec()
    value = receipt(bound, failed=failed)
    assert requests3362_receipt(result(value), bound, NONCE) == value
    assert value["fixture_lifecycle"] == "requires_separate_host_admission"


@pytest.mark.parametrize("change", [
    {"server_count": 0, "servers": []}, {"server_count": 1}, {"server_count": True},
    {"servers_stopped": False}, {"httpbin_ca_bundle_observed": False}, {"ca_overlay": True},
    {"fixture_digest": "f" * 64}, {"runtime_digest": "f" * 64}, {"source_revision": "f" * 64},
    {"check_spec_digest": "f" * 64}, {"certificate_files": {}},
    {"certificate_policy": {"common_name": "localhost", "dns_sans": [], "ip_sans": []}},
    {"health_assurance": "health_passed"}, {"extra": "field"},
])
def test_missing_lifecycle_wrong_bindings_overlay_or_invented_health_refuses(change):
    bound = spec()
    value = receipt(bound)
    value["requests3362_fixture"].update(change)
    assert requests3362_receipt(result(value), bound, NONCE) is None


@pytest.mark.parametrize("change", [
    {"host": "httpbin.org"}, {"protocol": "https"}, {"port": 0}, {"port": 65536}, {"port": True},
    {"stopped": False}, {"fixture_name": "other"}, {"fixture_name": []}, {"extra": True},
])
def test_wrong_actual_server_observations_refuse(change):
    bound = spec()
    value = receipt(bound)
    value["requests3362_fixture"]["servers"][0].update(change)
    assert requests3362_receipt(result(value), bound, NONCE) is None


def test_only_one_fixture_kind_or_missing_ca_fixture_cannot_fabricate_acceptance():
    bound = spec()
    value = receipt(bound)
    value["requests3362_fixture"]["servers"][1] = copy.deepcopy(value["requests3362_fixture"]["servers"][0])
    assert requests3362_receipt(result(value), bound, NONCE) is None
    del value["requests3362_fixture"]
    assert requests3362_receipt(result(value), bound, NONCE) is None


@pytest.mark.parametrize("changes", [
    {"timed_out": True}, {"stdout_truncated": True}, {"stderr_truncated": True}, {"returncode": 125},
])
def test_unknown_output_or_failed_cleanup_cannot_complete(changes):
    bound = spec()
    value = receipt(bound)
    assert requests3362_receipt(result(value, **changes), bound, NONCE) is None


def test_task_variant_runtime_identity_and_nonce_are_fixed():
    bound = spec()
    with pytest.raises(ValueError):
        requests3362_args(replace(bound, fixture_identity=None), NONCE)
    with pytest.raises(ValueError):
        requests3362_args(bound.as_dict(), NONCE)
    with pytest.raises(ValueError):
        requests3362_args(bound, "bad")
    wrong_private = replace(bound.private_pytest_identity, task_id="psf__requests-1963", variant="function36-v3",
                            helper_sha256=reviewed_helper_inventory("function36-v3"))
    wrong_fixture = replace(bound.fixture_identity, task_id=wrong_private.task_id, variant=wrong_private.variant,
                            profile_sha256="2be37770e20e5379d6e1cbed7b67808308a7903fefb3ca70726c70b9280ecfde",
                            helper_sha256=reviewed_helper_inventory("function36-v3"),
                            certificate_files=(("/opt/p07-function-fixture/cacert.pem", "8" * 64),
                                               ("/opt/p07-function-fixture/cert.pem", "9" * 64)))
    with pytest.raises(ValueError, match="3362"):
        requests3362_args(replace(bound, task_id=wrong_private.task_id, private_pytest_identity=wrong_private,
                                 fixture_identity=wrong_fixture), NONCE)


def test_composition_anchor_drift_fails_closed(monkeypatch):
    monkeypatch.setattr("atomic_flow.check_requests3362.private_pytest_args", lambda *args: ["-c", "changed bootstrap"])
    with pytest.raises(ValueError, match="anchor"):
        requests3362_args(spec(), NONCE)
