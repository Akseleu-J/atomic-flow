import ast
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_requests863_python27_calibrate as worker  # noqa:E402
import p07_requests863_python27_health as health  # noqa:E402
import p07_requests863_service as service  # noqa:E402
import p07_requests3362_calibrate as common  # noqa:E402


def test_real_transport_is_separate_from_target_and_checked_before_exposing_source():
    files = {'module.py': 'a' * 64}
    certs = {p: 'b' * 64 for p in health.CERTIFICATE_PATHS}
    code = worker.bootstrap_source('psf__requests-863', files, certs)
    probe = health.bootstrap_source('psf__requests-863', files, certs, '-----BEGIN CERTIFICATE-----\nfixture')
    for source in (code, probe, service.CHILD.replace('CONFIG', '{}')):
        ast.parse(source)
        assert 'pathlib' not in source and ',flush=True)' not in source
    assert code.index('subprocess.Popen') < code.index("sys.path.insert(0,'/testbed')")
    assert 'try:\n    exit_code=pytest.main(argv,plugins=[recorder])\nfinally:' in code
    assert "'worker_class':'gthread'" in code
    assert 'ssl.PROTOCOL_SSLv23' in code
    assert 'os.killpg(process.pid,signal.SIGTERM)' in code
    assert 'group_still_present' in code
    assert 'requests.' not in service.CHILD and 'import requests' not in service.CHILD
    assert 'httpbin.app(environ,fixture_response)' in service.CHILD
    assert "if environ.get('HTTP_TRANSFER_ENCODING','').lower()=='chunked':" in service.CHILD
    assert "environ['wsgi.input_terminated']=True" in service.CHILD
    assert 'HTTP connection was silently reopened' in probe
    assert 'Transfer-Encoding' in probe and 'chunked' in probe
    assert "context.check_hostname" in probe and 'CERT_REQUIRED' in probe
    assert "sslv3" not in probe.lower()  # modern client health still verifies CA/TLS; target uses its original SSL API


@pytest.mark.parametrize('task', ['psf__requests-2674', 'other'])
def test_other_tasks_cannot_use_historical863_classifier(task):
    with pytest.raises(ValueError, match='unreviewed task/runtime'):
        common.classify_target({'instance_id': task}, '', 0, None, None, runtime_profile='python2713')


def test_fixture_refuses_unadmitted_certificate_paths():
    with pytest.raises(ValueError):
        service.server_source({'/tmp/cert.pem': 'a' * 64})


@pytest.mark.parametrize('transfer', ['', 'chunked'])
def test_wsgi_compatibility_passes_the_original_body_stream_through(transfer):
    function = next(node for node in ast.parse(service.CHILD).body if isinstance(node, ast.FunctionDef) and node.name == 'application')
    stream = io.BytesIO(b'actual-wire-body')
    environ = {'wsgi.input': stream, 'HTTP_TRANSFER_ENCODING': transfer, 'HTTP_HOST': 'httpbin.org', 'PATH_INFO': '/post'}
    observed = []

    def app(actual, respond):
        assert actual is environ and actual['wsgi.input'] is stream
        observed.append(actual['wsgi.input'].read())
        respond('200 OK', [('Content-Type', 'text/plain')])
        return [b'original-application-response']

    namespace = {'httpbin': SimpleNamespace(app=app)}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<host-owned-fixture-function>', 'exec'), namespace)
    headers = []
    response = namespace['application'](environ, lambda status, fields, exc=None: headers.extend(fields))
    assert observed == [b'actual-wire-body'] and response == [b'original-application-response']
    assert ('wsgi.input_terminated' in environ) == (transfer == 'chunked')
    assert headers == [('Content-Type', 'text/plain')]
