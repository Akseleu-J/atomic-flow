import ast
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_requests2674_python27_calibrate as worker  # noqa:E402
import p07_requests2674_python27_health as health  # noqa:E402
import p07_requests2674_python27_image as runtime  # noqa:E402
import p07_requests3362_calibrate as common  # noqa:E402


def test_generated_programs_support_python2_grammar_without_target_import_on_host():
    files = {'module.py': 'a' * 64}
    certs = {path: 'b' * 64 for path in health.CERTIFICATE_PATHS}
    code = worker.bootstrap_source('psf__requests-2674', files, certs)
    health_code = health.bootstrap_source('psf__requests-2674', files, certs, '-----BEGIN CERTIFICATE-----\nfixture')
    for source in (code, health_code, worker.TIMEOUT_PROBE, worker.default_ca_overlay_source('a' * 64, 'b' * 64)):
        ast.parse(source)
        assert 'pathlib' not in source
        assert 'PROTOCOL_TLS_SERVER' not in source
        assert ',daemon=True' not in source
        assert ',flush=True)' not in source
    assert code.count('pytest.main(') == 1
    assert "argv=[str(value) for value in payload['plugins']]" in code
    assert 'try:\n    exit_code=pytest.main(argv,plugins=[recorder])\nfinally:' in code
    assert "['-rA','-v']" in code
    assert code.index("import httpbin,pkg_resources") < code.index("sys.path.insert(0,'/testbed')")
    assert "sys.flags.ignore_environment" in code
    assert "urllib2.ProxyHandler({})" in health_code
    assert "except socket.error as error:" in health_code
    assert "error.errno!=errno.ECONNREFUSED" in health_code
    assert "socket.getaddrinfo(host,None,0,0,socket.IPPROTO_TCP)" in health_code
    assert "context.check_hostname" in health_code and "CERT_REQUIRED" in health_code
    assert "wrong_ca" in health_code and "servers_stopped" in health_code


def test_python2_runtime_classifier_cannot_be_borrowed_for_another_task():
    with pytest.raises(ValueError, match='unreviewed task/runtime'):
        common.classify_target({'instance_id': 'psf__requests-863'}, '', 0, None, None, runtime_profile='python27')
    with pytest.raises(ValueError, match='unreviewed task/runtime'):
        common.classify_target({'instance_id': 'psf__requests-2674'}, '', 0, None, None, runtime_profile='arbitrary')


def test_native_health_is_bound_to_prospective_profile():
    assert health.PROFILE_SHA256 == runtime.PROFILE_SHA
    assert worker.PROFILE_SHA256 == runtime.PROFILE_SHA
    with pytest.raises(ValueError):
        health.bound_inputs('psf__requests-863', {'a.py': 'a' * 64}, {p: 'b' * 64 for p in health.CERTIFICATE_PATHS})


def test_v3_adds_only_omitted_protected_requirements_and_preserves_all_source_bytes():
    root = Path(__file__).resolve().parents[1]
    old = json.loads((root / 'benchmarks/stage4/corpus-p07-v2.json').read_bytes())
    new = json.loads((root / 'benchmarks/stage4/corpus-p07-v3.json').read_bytes())
    additions = {
        'psf__requests-863': 'tests/test_requests.py::RequestsTestSuite::test_allow_list_of_hooks_to_register_hook',
        'psf__requests-2674': 'test_requests.py::test_urllib3_pool_connection_closed',
    }
    assert len(old['tasks']) == len(new['tasks']) == 30
    for before, after in zip(old['tasks'], new['tasks'], strict=True):
        expected = dict(before)
        if before['instance_id'] in additions:
            node = additions[before['instance_id']]
            assert '+    def ' + node.rsplit('::', 1)[-1] in before['test_patch'] or '+def ' + node.rsplit('::', 1)[-1] in before['test_patch']
            expected['FAIL_TO_PASS'] = before['FAIL_TO_PASS'] + [node]
        assert after == expected
