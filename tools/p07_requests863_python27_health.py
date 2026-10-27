"""Fixed function-URL service health source and validator, without an executor.

The caller must independently admit its image, source, private runtime, DNS SAN
certificate, inventory and Docker/network policy. This module cannot qualify an
image or launch a worker. Its program executes only on that admitted worker.
"""
from __future__ import annotations

import hashlib
import json
import re

import p07_requests863_python27_image as runtime
import p07_requests863_service as wsgi
from p07_requests863_wire_contracts import HOSTS

PREFIX = "/opt/p07-python27/lib/python2.7/site-packages/"
FIXTURE_ROOT = "/opt/p07-function-fixture/"
CERTIFICATE_PATHS = tuple(FIXTURE_ROOT + name for name in ("cacert.pem", "cert.pem", "key.pem"))
TASK_HOSTS = {'psf__requests-863': {host: '127.0.0.1' for host in HOSTS}}
URLS = {"http": "http://httpbin.org/", "https": "https://httpbin.org/"}
RESULT_PREFIX = "P07_FUNCTION_HEALTH="
PROFILE_SHA256 = runtime.PROFILE_SHA


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_sha(value):
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def bound_inputs(task_id, installed_files, certificate_files):
    if task_id not in TASK_HOSTS:
        raise ValueError("one reviewed function-URL task required")
    if not isinstance(installed_files, dict) or not 1 <= len(installed_files) <= 5000:
        raise ValueError("bounded host-admitted installed inventory required")
    for path, digest in installed_files.items():
        if (not isinstance(path, str) or len(path) > 512 or path.startswith("/")
                or any(part in {"", ".", ".."} for part in path.split("/")) or "\\" in path or ":" in path
                or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
            raise ValueError("confined installed inventory path and digest required")
    if (not isinstance(certificate_files, dict) or set(certificate_files) != set(CERTIFICATE_PATHS)
            or any(not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                   for digest in certificate_files.values())):
        raise ValueError("exact three fixed certificate paths and digests required")
    return {"task_id": task_id, "profile_sha256": PROFILE_SHA256, "files": installed_files, "certificate_files": certificate_files,
            "host_mappings": TASK_HOSTS[task_id], "urls": URLS}


def bootstrap_source(task_id, installed_files, certificate_files, wrong_ca):
    """Return fixed Python2.7 code; this does not admit or execute its inputs."""
    data = bound_inputs(task_id, installed_files, certificate_files)
    if not isinstance(wrong_ca, str) or not 1 <= len(wrong_ca) <= 16384 or "-----BEGIN CERTIFICATE-----" not in wrong_ca:
        raise ValueError("bounded unrelated public test CA required")
    data = {**data, "input_sha256": canonical_sha(data), "wrong_ca": wrong_ca}
    return r'''from __future__ import print_function
import base64,errno,hashlib,json,os,socket,ssl,sys,threading,urllib,urllib2
from contextlib import closing
data=json.loads(DATA)
prefix='/opt/p07-python27/lib/python2.7/site-packages/'
fixture='/opt/p07-function-fixture/'
if list(sys.version_info[:3])!=[2,7,13] or sys.prefix!='/opt/p07-python27' or not (sys.flags.ignore_environment and sys.flags.no_user_site and sys.flags.no_site):
    raise RuntimeError('private interpreter identity mismatch')
if os.getcwd()!='/tmp' or any(str(path).startswith('/testbed') for path in sys.path):
    raise RuntimeError('target source import path exposed')
for relative,expected in data['files'].items():
    path=prefix+relative
    if os.path.islink(path) or not os.path.realpath(path).startswith(prefix) or hashlib.sha256(open(path,'rb').read()).hexdigest()!=expected:
        raise RuntimeError('admitted installed package bytes differ')
for absolute,expected in data['certificate_files'].items():
    path=absolute
    if os.path.islink(path) or os.path.realpath(path)!=absolute or hashlib.sha256(open(path,'rb').read()).hexdigest()!=expected:
        raise RuntimeError('admitted service certificate bytes differ')
decoded=ssl._ssl._test_decode_cert(fixture+'cert.pem')
common_names=[value for group in decoded['subject'] for key,value in group if key=='commonName']
if common_names!=['httpbin.org'] or decoded.get('subjectAltName')!=(('DNS','httpbin.org'),('DNS','github.com')):
    raise RuntimeError('fixed DNS-SAN service certificate identity mismatch')
actual_hosts={}
for host,address in data['host_mappings'].items():
    actual=sorted(set(item[4][0] for item in socket.getaddrinfo(host,None,0,0,socket.IPPROTO_TCP)))
    if actual!=[address]:
        raise RuntimeError('fixed service host mapping mismatch')
    actual_hosts[host]=actual
os.environ['HTTPBIN_URL']='http://httpbin.org/'
import pkg_resources,httpbin
if pkg_resources.get_distribution('httpbin').version!='0.5.0' or not os.path.realpath(httpbin.__file__).startswith(prefix):
    raise RuntimeError('HTTPBin private import/version mismatch')
class NoRedirect(urllib2.HTTPRedirectHandler):
    def redirect_request(self,request,response,code,message,headers,newurl):
        return None
def fetch(url,context=None,authorization=None,redirect=True):
    handlers=[urllib2.ProxyHandler({})]
    if context is not None:
        handlers.append(urllib2.HTTPSHandler(context=context))
    if not redirect:
        handlers.append(NoRedirect())
    request=urllib2.Request(url)
    if authorization is not None:
        request.add_header('Authorization',authorization)
    return closing(urllib2.build_opener(*handlers).open(request,timeout=3))
checks={}
try:
    try:
        closed_probe=socket.create_connection(('127.0.0.1',1),timeout=1)
    except socket.error as error:
        if error.errno!=errno.ECONNREFUSED:raise
        checks['port1']='connection_refused'
    else:
        closed_probe.close()
        raise RuntimeError('port1 unexpectedly open')
START_SERVERS
    context=ssl.create_default_context(cafile=fixture+'cacert.pem')
    if not context.check_hostname or context.verify_mode!=ssl.CERT_REQUIRED:
        raise RuntimeError('client TLS chain or hostname verification disabled')
    for scheme in ('http','https'):
        with fetch(data['urls'][scheme]+'get?health=p07',context if scheme=='https' else None) as response:
            payload=json.loads(response.read(65536).decode())
            if response.code!=200 or payload.get('args')!={'health':'p07'}:
                raise RuntimeError('real HTTPBin response mismatch')
        checks[scheme]={'status':200,'args':{'health':'p07'}}
    authorization='Basic '+base64.b64encode(b'p07:fixture').decode()
    with fetch(data['urls']['http']+'basic-auth/p07/fixture',authorization=authorization) as response:
        payload=json.loads(response.read(65536).decode())
        if response.code!=200 or payload.get('authenticated') is not True or payload.get('user')!='p07':
            raise RuntimeError('real HTTPBin basic authentication mismatch')
    checks['basic_auth']={'status':200,'authenticated':True,'user':'p07'}
    wrong_path='/tmp/p07-function-wrongca.pem'
    with open(wrong_path,'w') as stream:stream.write(data['wrong_ca'])
    wrong=ssl.create_default_context(cafile=str(wrong_path))
    try:
        with fetch(data['urls']['https']+'get?health=p07',wrong) as response:
            response.read(1)
    except urllib2.URLError as error:
        if not isinstance(error.reason,(ssl.SSLError,ssl.CertificateError)):
            raise
        checks['wrong_ca']='certificate_verification_failed'
    else:
        raise RuntimeError('unrelated CA unexpectedly accepted')
    with fetch('https://github.com/',context) as response:
        if response.code!=200 or response.info().getheader('Set-Cookie')!='p07_fixture=public; Path=/; HttpOnly':
            raise RuntimeError('cross-domain cookie fixture differs')
        response.read(65536)
    checks['github_cookie']={'status':200,'cookie':'p07_fixture=public; Path=/; HttpOnly'}
    import httplib
    connection=httplib.HTTPConnection('httpbin.org',80,timeout=3)
    connection.request('GET','/get',headers={'Connection':'keep-alive'})
    response=connection.getresponse()
    if response.status!=200 or response.getheader('Connection').lower()!='keep-alive':raise RuntimeError('keep-alive header absent')
    response.read()
    endpoint=connection.sock.getsockname()
    connection.request('GET','/get',headers={'Connection':'close'})
    if connection.sock.getsockname()!=endpoint:raise RuntimeError('HTTP connection was silently reopened')
    response=connection.getresponse()
    if response.status!=200 or response.getheader('Connection').lower()!='close':raise RuntimeError('close header absent')
    response.read()
    connection.close()
    checks['http11_keepalive_close']='real_persistent_connection'
    connection=httplib.HTTPConnection('httpbin.org',80,timeout=3)
    connection.putrequest('POST','/post')
    connection.putheader('Transfer-Encoding','chunked')
    connection.putheader('Content-Type','text/plain')
    connection.endheaders()
    connection.send(b'3\r\nabc\r\n0\r\n\r\n')
    response=connection.getresponse()
    payload=json.loads(response.read())
    connection.close()
    if response.status!=200 or payload.get('data')!='abc':raise RuntimeError('real chunked WSGI input differs: '+repr((response.status,payload)))
    checks['chunked_request']='abc'
    legacy=ssl.SSLContext(ssl.PROTOCOL_SSLv3)
    legacy.verify_mode=ssl.CERT_REQUIRED
    legacy.load_verify_locations(fixture+'cacert.pem')
    legacy.check_hostname=True
    with fetch('https://httpbin.org/get?health=p07',legacy) as response:
        if response.code!=200 or json.loads(response.read()).get('args')!={'health':'p07'}:raise RuntimeError('real CA-verified SSLv3 service failed')
    checks['legacy_sslv3']='verified_chain_and_hostname'
    for password,expected in (('pass',200),('wrong',401)):
        passwords=urllib2.HTTPPasswordMgrWithDefaultRealm()
        passwords.add_password(None,'http://httpbin.org/digest-auth/auth/user/pass','user',password)
        opener=urllib2.build_opener(urllib2.ProxyHandler({}),urllib2.HTTPDigestAuthHandler(passwords))
        try:
            response=opener.open('http://httpbin.org/digest-auth/auth/user/pass',timeout=3)
        except urllib2.HTTPError as error:
            if expected!=401 or error.code!=401:raise
            error.close()
        else:
            payload=json.loads(response.read())
            response.close()
            if expected!=200 or payload!={'authenticated':True,'user':'user'}:raise RuntimeError('Digest response validation differs')
    checks['digest']='real_response_hash_validated_wrong_password_rejected'
    try:
        with fetch('https://kennethreitz.com/',context) as response:response.read(1)
    except urllib2.URLError as error:
        if not isinstance(error.reason,(ssl.SSLError,ssl.CertificateError)):raise
        checks['wrong_hostname']='certificate_verification_failed'
    else:raise RuntimeError('mismatched certificate hostname accepted')


finally:
STOP_SERVERS
stopped=len(function_processes)==2 and all(process.poll() is not None for process in function_processes)
if not stopped:
    raise RuntimeError('real HTTPBin server cleanup unconfirmed')
target_imports=[name for name,module in list(sys.modules.items()) if str(getattr(module,'__file__','')).startswith('/testbed/')]
if target_imports or 'requests' in sys.modules:
    raise RuntimeError('health imported target Requests')
result={'task_id':data['task_id'],'profile_sha256':data['profile_sha256'],'input_sha256':data['input_sha256'],'version_info':list(sys.version_info[:3]),
    'prefix':sys.prefix,'executable':sys.executable,'confinement':{'ignore_environment':sys.flags.ignore_environment,'no_user_site':sys.flags.no_user_site,'no_site':sys.flags.no_site},
    'httpbin_version':'0.5.0','httpbin_module':httpbin.__file__,'host_mappings':actual_hosts,
    'urls':data['urls'],'ports':[80,443],'server_host':'127.0.0.1','checks':checks,
    'servers_stopped':stopped,'target_imports':target_imports,'requests_imported':False,
    'target_setup_or_tests_executed':False,'calibrated':False,'verified_success':False}
print('P07_FUNCTION_HEALTH='+json.dumps(result,sort_keys=True))
'''.replace("DATA", repr(json.dumps(data, sort_keys=True))).replace("START_SERVERS", ''.join('    '+line+'\n' for line in wsgi.server_source(certificate_files).splitlines())).replace("STOP_SERVERS", ''.join('    '+line+'\n' for line in wsgi.STOP.splitlines()))


def validate_health(text, task_id, installed_files, certificate_files):
    bound = bound_inputs(task_id, installed_files, certificate_files)
    rows = [line[len(RESULT_PREFIX):] for line in text.splitlines() if line.startswith(RESULT_PREFIX)]
    if len(rows) != 1:
        raise ValueError("one complete function service health receipt required")
    result = json.loads(rows[0])
    checks = {scheme: {"status": 200, "args": {"health": "p07"}} for scheme in ("http", "https")}
    checks.update(basic_auth={"status": 200, "authenticated": True, "user": "p07"}, wrong_ca="certificate_verification_failed",
                  port1="connection_refused")
    checks.update(github_cookie={"status": 200, "cookie": "p07_fixture=public; Path=/; HttpOnly"},
                  http11_keepalive_close="real_persistent_connection", chunked_request="abc",
                  legacy_sslv3="verified_chain_and_hostname", wrong_hostname="certificate_verification_failed",
                  digest="real_response_hash_validated_wrong_password_rejected")
    if (result.get("task_id") != task_id or result.get("profile_sha256") != PROFILE_SHA256
            or result.get("input_sha256") != canonical_sha(bound)
            or result.get("version_info") != [2, 7, 13] or result.get("prefix") != "/opt/p07-python27"
            or result.get("executable") != "/opt/p07-python27/bin/python2.7" or result.get("confinement") != {"ignore_environment": 1, "no_user_site": 1, "no_site": 1}
            or result.get("httpbin_version") != "0.5.0" or not result.get("httpbin_module", "").startswith(PREFIX)
            or result.get("host_mappings") != {host: [address] for host, address in TASK_HOSTS[task_id].items()}
            or result.get("urls") != URLS or result.get("ports") != [80, 443] or result.get("server_host") != "127.0.0.1"
            or result.get("checks") != checks or result.get("servers_stopped") is not True
            or result.get("target_imports") != [] or result.get("requests_imported") is not False
            or result.get("target_setup_or_tests_executed") is not False
            or result.get("calibrated") is not False or result.get("verified_success") is not False):
        raise ValueError("function service health completion or bound identity mismatch")
    return result
