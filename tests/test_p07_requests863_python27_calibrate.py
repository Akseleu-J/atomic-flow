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
    assert code.index("for plugin_name in ('pytest_httpbin.plugin','pytest_mock'):") < code.index('subprocess.Popen')
    assert "sys.modules['six.moves']=" not in code and 'sys.modules["six.moves"]=' not in code
    assert 'try:\n    exit_code=pytest.main(argv,plugins=[recorder])\nfinally:' in code
    assert "'worker_class':'gthread'" in code
    assert 'ssl.PROTOCOL_SSLv23' in code
    assert 'context.options &= ~ssl.OP_NO_SSLv3' in code
    assert "self.cfg.set('worker_class','__main__.FixtureThreadWorker')" in service.CHILD
    assert 'ssl.wrap_socket=' not in code and 'ssl.SSLContext=' not in code
    assert 'os.kill(process.pid,signal.SIGTERM)' in code
    assert 'os.killpg(process.pid,signal.SIGKILL)' in code
    assert 'group_still_present' in code
    assert 'requests.' not in service.CHILD and 'import requests' not in service.CHILD
    assert 'httpbin.app(environ,fixture_response)' in service.CHILD
    assert "if environ.get('HTTP_TRANSFER_ENCODING','').lower()=='chunked':" in service.CHILD
    assert "environ['wsgi.input_terminated']=True" in service.CHILD
    assert 'HTTP connection was silently reopened' in probe
    assert 'Transfer-Encoding' in probe and 'chunked' in probe
    assert "context.check_hostname" in probe and 'CERT_REQUIRED' in probe
    assert "legacy.verify_mode=ssl.CERT_REQUIRED" in probe
    assert "legacy.check_hostname=True" in probe


@pytest.mark.parametrize('task', ['psf__requests-2674', 'other'])
def test_other_tasks_cannot_use_historical863_classifier(task):
    with pytest.raises(ValueError, match='unreviewed task/runtime'):
        common.classify_target({'instance_id': task}, '', 0, None, None, runtime_profile='python2713')


def test_fixture_refuses_unadmitted_certificate_paths():
    with pytest.raises(ValueError):
        service.server_source({'/tmp/cert.pem': 'a' * 64})


def test_fixture_worker_requires_inspected_init_without_relaxing_other_isolation():
    image = 'sha256:' + 'a' * 64
    config = {'Image': image, 'Mounts': [], 'HostConfig': {
        'Init': True, 'NetworkMode': 'none', 'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges'],
        'Memory': 6 * 1024**3, 'NanoCpus': 2_000_000_000, 'PidsLimit': 256,
        'Sysctls': {'net.ipv4.ip_unprivileged_port_start': '0'},
        'ExtraHosts': [host + ':' + address for host, address in health.TASK_HOSTS['psf__requests-863'].items()],
    }}
    worker.inspect_policy(config, image, 'psf__requests-863')
    config['HostConfig']['Init'] = False
    with pytest.raises(ValueError, match='transport policy'):
        worker.inspect_policy(config, image, 'psf__requests-863')


@pytest.mark.parametrize('transfer', ['', 'chunked'])
def test_wsgi_compatibility_passes_the_original_body_stream_through(transfer):
    function = next(node for node in ast.parse(service.CHILD).body if isinstance(node, ast.FunctionDef) and node.name == 'historical_application')
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
    response = namespace['historical_application'](environ, lambda status, fields, exc=None: headers.extend(fields))
    assert observed == [b'actual-wire-body'] and response == [b'original-application-response']
    assert ('wsgi.input_terminated' in environ) == (transfer == 'chunked')
    assert headers == [('Content-Type', 'text/plain')]


def test_legacy_tls_changes_only_a_fresh_fixture_context_and_preserves_parser_reuse():
    calls = []

    class Parent:
        def enqueue_req(self, connection):
            calls.append(('enqueue', connection.parser))
            return 'submitted'

    class Context:
        options = 7

        def __init__(self, protocol):
            calls.append(('context', protocol))

        def load_cert_chain(self, cert, key):
            calls.append(('chain', cert, key))

        def set_ciphers(self, ciphers):
            calls.append(('ciphers', ciphers))

        def wrap_socket(self, sock, **kwargs):
            assert self.options == 5 and kwargs['server_side'] is True
            return sock

    tree = ast.parse(service.CHILD.replace('CONFIG', '{}'))
    worker = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'FixtureThreadWorker')
    namespace = {'ThreadWorker': Parent, 'ssl': SimpleNamespace(SSLContext=Context, PROTOCOL_SSLv23=23, OP_NO_SSLv3=2),
                 'http': SimpleNamespace(RequestParser=lambda cfg, sock: 'original-gunicorn-parser')}
    exec(compile(ast.Module(body=[worker], type_ignores=[]), '<host-owned-tls-worker>', 'exec'), namespace)
    instance = namespace['FixtureThreadWorker']()
    instance.cfg = SimpleNamespace(is_ssl=True, certfile='fixture.cert', keyfile='fixture.key', ciphers='AES128-SHA',
                                   do_handshake_on_connect=False, suppress_ragged_eofs=True)
    connection = SimpleNamespace(parser=None, sock=SimpleNamespace(setblocking=lambda flag: None))
    assert instance.enqueue_req(connection) == 'submitted'
    assert instance.enqueue_req(connection) == 'submitted'
    assert calls.count(('context', 23)) == 1
    assert calls.count(('enqueue', 'original-gunicorn-parser')) == 2
    assert Context.options == 7


@pytest.mark.parametrize('method,host,path,custom', [
    ('HEAD', 't.co', '/NFrx0zLG', True),
    ('GET', 't.co', '/NFrx0zLG', False),
    ('HEAD', 'httpbin.org', '/NFrx0zLG', False),
    ('HEAD', 't.co', '/different', False),
])
def test_chunked_head_fixture_is_confined_to_its_wire_scenario(method, host, path, custom):
    class Parent:
        def handle_request(self, request, connection):
            return 'original-gunicorn-handler'

    worker = next(node for node in ast.parse(service.CHILD.replace('CONFIG', '{}')).body
                  if isinstance(node, ast.ClassDef) and node.name == 'FixtureThreadWorker')
    namespace = {'ThreadWorker': Parent, 'formatdate': lambda **kwargs: 'fixed test date'}
    exec(compile(ast.Module(body=[worker], type_ignores=[]), '<host-owned-wire-worker>', 'exec'), namespace)
    wire = []
    request = SimpleNamespace(method=method, path=path, headers=[('HOST', host)])
    connection = SimpleNamespace(sock=SimpleNamespace(sendall=wire.append))
    result = namespace['FixtureThreadWorker']().handle_request(request, connection)
    if not custom:
        assert result == 'original-gunicorn-handler' and not wire
    else:
        assert result is False
        headers, body = b''.join(wire).split(b'\r\n\r\n', 1)
        assert b'Transfer-Encoding: chunked' in headers and b'Content-Length' not in headers and body == b''
