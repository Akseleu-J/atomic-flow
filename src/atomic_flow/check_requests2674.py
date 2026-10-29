"""Fixed requests2674 same-container services, resource and ordinary receipts.

Pure source composition only. The owned controller must independently check
candidate/execution manifests and resource bytes before and after execution.
Target reports remain observations, not independent grading or attestation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import fields

from atomic_flow.check_fixture import ExecutionOverlay
from atomic_flow.check_private_pytest import private_pytest_args, private_pytest_receipt
from atomic_flow.check_requests3362 import _replace
from atomic_flow.check_requests_resources import RequestsCAIdentity
from atomic_flow.check_spec import CheckSpec

POLICY = "requests2674-python27-owned-lifecycle/1"


def health_policy(fixture, ca):
    return {"policy": POLICY, "task_id": fixture.task_id, "certificate_policy": fixture.certificate_policy,
            "host_mappings": dict(fixture.host_mappings), "ports": [80, 443],
            "tls_ca_source": "/testbed/requests/cacert.pem", "wrong_ca_sha256": ca.wrong_ca_sha256,
            "checks": ["http", "https", "basic_auth", "wrong_ca", "port1", "mapped_http_alias"],
            "target_imports_before_health": False}


def health_bytes(fixture, ca):
    return json.dumps(health_policy(fixture, ca), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _binding(spec, overlay):
    if (type(spec) is not CheckSpec or type(overlay) is not ExecutionOverlay
            or type(spec.requests_ca_identity) is not RequestsCAIdentity
            or spec.fixture_identity is None or spec.fixture_identity.variant != "2674-v2"
            or spec.private_pytest_identity is None or spec.private_pytest_identity.variant != "2674-v2"
            or spec.runtime_identity is None or spec.runtime_identity.python_version != (2, 7, 18)):
        raise ValueError("exact requests2674 spec, resource and execution overlay required")
    ca, fixture = spec.requests_ca_identity, spec.fixture_identity
    if (overlay.task_id != spec.task_id or overlay.check_spec_digest != spec.digest
            or overlay.fixture_digest != fixture.digest or overlay.runtime_digest != spec.runtime_identity.digest
            or overlay.candidate_core_revision != spec.source_revision or overlay.execution_core_revision != spec.source_revision
            or overlay.original_ca_sha256 != ca.original_ca_sha256 or overlay.fixture_ca_sha256 != ca.fixture_ca_sha256
            or overlay.result_ca_sha256 != ca.result_ca_sha256
            or hashlib.sha256(health_bytes(fixture, ca)).hexdigest() != fixture.health_policy_sha256):
        raise ValueError("requests2674 resource/health binding differs from admitted spec")
    return fixture, ca


_SETUP = r'''
import base64,errno,socket,ssl,threading,urllib2
from contextlib import closing
function_payload=json.loads(FUNCTION_JSON)
function_fixture=function_payload['fixture']
function_ca=function_payload['ca']
function_overlay=function_payload['overlay']
function_servers=[]
function_threads=[]
function_stopped=False
function_checks={}
for absolute,expected in function_fixture['certificate_files'].items():
    checked_file(absolute,expected,16384)
function_root='/opt/p07-function-fixture/'
function_execution_ca=str(root)+'/requests/cacert.pem'
checked_file(function_execution_ca,function_ca['result_ca_sha256'],1000000)
decoded=ssl._ssl._test_decode_cert(function_root+'cert.pem')
common_names=[value for group in decoded.get('subject',()) for key,value in group if key=='commonName']
if common_names!=['httpbin.org'] or decoded.get('subjectAltName')!=(('DNS','httpbin.org'),):
    raise SystemExit(125)
for host,address in function_fixture['host_mappings'].items():
    actual=sorted(set(item[4][0] for item in socket.getaddrinfo(str(host),None,0,0,socket.IPPROTO_TCP)))
    if actual!=[address]:raise SystemExit(125)
for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
    os.environ.pop(name,None)
os.environ['HTTPBIN_URL']='http://httpbin.org/'
os.environ['REQUESTS_CA_BUNDLE']=function_root+'cacert.pem'
os.environ['CURL_CA_BUNDLE']=function_root+'cacert.pem'
import httpbin,pkg_resources
if (pkg_resources.get_distribution('httpbin').version!='0.5.0'
        or not os.path.realpath(httpbin.__file__).startswith(site)):
    raise SystemExit(125)
from wsgiref.simple_server import make_server

def function_stop():
    global function_stopped
    stopped=True
    for index,server in enumerate(function_servers):
        try:
            if index<len(function_threads) and function_threads[index].is_alive():server.shutdown()
            server.server_close()
        except BaseException:stopped=False
    for thread in function_threads:
        thread.join(3)
        if thread.is_alive():stopped=False
    function_stopped=stopped and len(function_servers)==len(function_threads)==2

def function_fetch(url,context=None,authorization=None):
    handlers=[urllib2.ProxyHandler({})]
    if context is not None:handlers.append(urllib2.HTTPSHandler(context=context))
    request=urllib2.Request(url)
    if authorization is not None:request.add_header('Authorization',authorization)
    with closing(urllib2.build_opener(*handlers).open(request,timeout=3)) as response:
        raw=response.read(65537)
        if len(raw)>65536:raise RuntimeError('health response bound exceeded')
        return response.code,json.loads(raw.decode())

try:
    try:
        probe=socket.create_connection(('127.0.0.1',1),timeout=1)
    except socket.error as error:
        if error.errno!=errno.ECONNREFUSED:raise
        function_checks['port1']='connection_refused'
    else:
        probe.close()
        raise RuntimeError('closed fixture port unexpectedly open')
    function_http=make_server('127.0.0.1',80,httpbin.app)
    function_servers.append(function_http)
    function_https=make_server('127.0.0.1',443,httpbin.app)
    function_servers.append(function_https)
    server_context=ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
    server_context.load_cert_chain(function_root+'cert.pem',function_root+'key.pem')
    function_https.socket=server_context.wrap_socket(function_https.socket,server_side=True)
    for server in function_servers:
        thread=threading.Thread(target=server.serve_forever)
        thread.daemon=True
        function_threads.append(thread)
        thread.start()
    client=ssl.create_default_context(cafile=function_execution_ca)
    if not client.check_hostname or client.verify_mode!=ssl.CERT_REQUIRED:raise SystemExit(125)
    for scheme in ('http','https'):
        status,result=function_fetch(scheme+'://httpbin.org/get?health=p07',client if scheme=='https' else None)
        if status!=200 or result.get('args')!={'health':'p07'}:raise RuntimeError('HTTPBin health mismatch')
        function_checks[scheme]={'status':status,'args':result['args']}
    authorization='Basic '+base64.b64encode(b'p07:fixture').decode()
    status,result=function_fetch('http://httpbin.org/basic-auth/p07/fixture',authorization=authorization)
    if status!=200 or result.get('authenticated') is not True or result.get('user')!='p07':
        raise RuntimeError('HTTPBin basic auth mismatch')
    function_checks['basic_auth']={'status':status,'authenticated':True,'user':'p07'}
    wrong_path='/tmp/p07-owned-function-wrongca.pem'
    with open(wrong_path,'wb') as stream:stream.write(function_ca['wrong_ca_pem'].encode('ascii'))
    checked_file(wrong_path,function_ca['wrong_ca_sha256'],16384)
    wrong=ssl.create_default_context(cafile=wrong_path)
    try:function_fetch('https://httpbin.org/get?health=p07',wrong)
    except urllib2.URLError as error:
        if not isinstance(error.reason,(ssl.SSLError,ssl.CertificateError)):raise
        function_checks['wrong_ca']='certificate_verification_failed'
    else:raise RuntimeError('unrelated CA unexpectedly accepted')
    if 'www.google.co.uk' in function_fixture['host_mappings']:
        status,result=function_fetch('http://www.google.co.uk/get?health=p07')
        if status!=200 or result.get('args')!={'health':'p07'}:raise RuntimeError('mapped alias health mismatch')
        function_checks['mapped_http_alias']={'host':'www.google.co.uk','status':status}
    else:function_checks['mapped_http_alias']='not_in_task_profile'
except BaseException:
    function_stop()
    raise
'''

_MAIN = r'''try:
    code=int(pytest.main(arguments,plugins=[evidence]))
finally:
    function_stop()
if not function_stopped:raise SystemExit(125)
checked_file(function_execution_ca,function_ca['result_ca_sha256'],1000000)'''

_OBSERVATION = r'''receipt['requests_function_fixture']={
    'policy':FUNCTION_POLICY,'fixture_digest':FUNCTION_FIXTURE_DIGEST,
    'runtime_digest':payload['runtime_digest'],'check_spec_digest':payload['check_spec_digest'],
    'source_revision':payload['source_revision'],'ca_identity_digest':FUNCTION_CA_DIGEST,
    'execution_overlay':function_overlay,'certificate_files':function_fixture['certificate_files'],
    'host_mappings':function_fixture['host_mappings'],'ports':[80,443],
    'servers_stopped':function_stopped,'health':function_checks,
    'health_before_target_import':True,'execution_ca_sha256':function_ca['result_ca_sha256'],
    'key_material_binding':'admitted_immutable_image_and_provisioning_receipt'}
'''


def function_args(spec, nonce, overlay):
    fixture, ca = _binding(spec, overlay)
    source = private_pytest_args(spec, spec.private_pytest_identity, nonce)[1]
    data = {"fixture": fixture.as_dict(), "ca": ca.as_dict(), "overlay": overlay.as_dict()}
    setup = _SETUP.replace("FUNCTION_JSON", repr(json.dumps(data, sort_keys=True, ensure_ascii=True)))
    source = _replace(source, "entries=0\n", setup + "\nentries=0\n")
    source = _replace(source, "code=int(pytest.main(arguments,plugins=[evidence]))", _MAIN)
    observed = (_OBSERVATION.replace("FUNCTION_POLICY", repr(POLICY))
                .replace("FUNCTION_FIXTURE_DIGEST", repr(fixture.digest)).replace("FUNCTION_CA_DIGEST", repr(ca.digest)))
    source = _replace(source, "print('\\nATOMIC_CHECK_SPEC_RECEIPT:'+json.dumps(receipt,sort_keys=True))",
                      observed + "print('\\nATOMIC_CHECK_SPEC_RECEIPT:'+json.dumps(receipt,sort_keys=True))")
    return ["-c", source]


def function_receipt(result, spec, nonce):
    value = private_pytest_receipt(result, spec, spec.private_pytest_identity, nonce)
    if value is None:
        return None
    observed = value.get("requests_function_fixture")
    if type(observed) is not dict or type(observed.get("execution_overlay")) is not dict:
        return None
    raw = observed["execution_overlay"]
    try:
        overlay = ExecutionOverlay(**{field.name: raw[field.name] for field in fields(ExecutionOverlay)})
        fixture, ca = _binding(spec, overlay)
    except (KeyError, TypeError, ValueError):
        return None
    checks = {scheme: {"status": 200, "args": {"health": "p07"}} for scheme in ("http", "https")}
    checks.update(basic_auth={"status": 200, "authenticated": True, "user": "p07"},
                  wrong_ca="certificate_verification_failed", port1="connection_refused",
                  mapped_http_alias={"host": "www.google.co.uk", "status": 200} if len(fixture.host_mappings) == 2
                  else "not_in_task_profile")
    expected = {"policy": POLICY, "fixture_digest": fixture.digest, "runtime_digest": spec.runtime_identity.digest,
                "check_spec_digest": spec.digest, "source_revision": spec.source_revision,
                "ca_identity_digest": ca.digest, "execution_overlay": overlay.as_dict(),
                "certificate_files": dict(fixture.certificate_files), "host_mappings": dict(fixture.host_mappings),
                "ports": [80, 443], "servers_stopped": True, "health": checks, "health_before_target_import": True,
                "execution_ca_sha256": ca.result_ca_sha256,
                "key_material_binding": "admitted_immutable_image_and_provisioning_receipt"}
    # JSON canonical bytes distinguish booleans from integers recursively.
    try:
        matches = json.dumps(observed, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return None
    if not matches:
        return None
    return value
