"""Fixed requests863 same-container services, resource and ordinary receipts.

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

POLICY = "requests863-python27-owned-lifecycle/1"


def health_policy(fixture, ca):
    return {"policy": POLICY, "task_id": fixture.task_id, "certificate_policy": fixture.certificate_policy,
            "host_mappings": dict(fixture.host_mappings), "ports": [80, 443],
            "tls_ca_source": "/testbed/requests/cacert.pem", "wrong_ca_sha256": ca.wrong_ca_sha256,
            "checks": ["http", "https", "basic_auth", "wrong_ca", "port1", "github_cookie", "http11_keepalive_close",
                       "chunked_request", "chunked_head_redirect", "legacy_sslv3", "digest", "wrong_hostname"],
            "target_imports_before_health": False}


def health_bytes(fixture, ca):
    return json.dumps(health_policy(fixture, ca), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _binding(spec, overlay):
    if (type(spec) is not CheckSpec or type(overlay) is not ExecutionOverlay
            or type(spec.requests_ca_identity) is not RequestsCAIdentity
            or spec.fixture_identity is None or spec.fixture_identity.variant != "863-v7"
            or spec.private_pytest_identity is None or spec.private_pytest_identity.variant != "863-v7"
            or spec.runtime_identity is None or spec.runtime_identity.python_version != (2, 7, 13)):
        raise ValueError("exact requests863 spec, resource and execution overlay required")
    ca, fixture = spec.requests_ca_identity, spec.fixture_identity
    if (overlay.task_id != spec.task_id or overlay.check_spec_digest != spec.digest
            or overlay.fixture_digest != fixture.digest or overlay.runtime_digest != spec.runtime_identity.digest
            or overlay.candidate_core_revision != spec.source_revision or overlay.execution_core_revision != spec.source_revision
            or overlay.original_ca_sha256 != ca.original_ca_sha256 or overlay.fixture_ca_sha256 != ca.fixture_ca_sha256
            or overlay.result_ca_sha256 != ca.result_ca_sha256
            or hashlib.sha256(health_bytes(fixture, ca)).hexdigest() != fixture.health_policy_sha256):
        raise ValueError("requests863 resource/health binding differs from admitted spec")
    return fixture, ca


_SETUP = r'''
import base64,errno,socket,ssl,threading,urllib,urllib2
from contextlib import closing
function_payload=json.loads(FUNCTION_JSON)
function_fixture=function_payload['fixture']
function_ca=function_payload['ca']
function_overlay=function_payload['overlay']
function_execution_ca=str(root)+'/requests/cacert.pem'
for absolute,expected in function_fixture['certificate_files'].items():checked_file(absolute,expected,16384)
checked_file(function_execution_ca,function_ca['result_ca_sha256'],1000000)
fixture='/opt/p07-function-fixture/'
decoded=ssl._ssl._test_decode_cert(fixture+'cert.pem')
names=[v for group in decoded.get('subject',()) for k,v in group if k=='commonName']
if names!=['httpbin.org'] or decoded.get('subjectAltName')!=(('DNS','httpbin.org'),('DNS','github.com')):raise SystemExit(125)
for host,address in function_fixture['host_mappings'].items():
    actual=sorted(set(item[4][0] for item in socket.getaddrinfo(str(host),None,0,0,socket.IPPROTO_TCP)))
    if actual!=[address]:raise SystemExit(125)
for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):os.environ.pop(name,None)
data={'urls':function_fixture['urls'],'wrong_ca':function_ca['wrong_ca_pem']}
class NoRedirect(urllib2.HTTPRedirectHandler):
    def redirect_request(self,request,response,code,message,headers,newurl):return None
def fetch(url,context=None,authorization=None,redirect=True):
    handlers=[urllib2.ProxyHandler({})]
    if context is not None:handlers.append(urllib2.HTTPSHandler(context=context))
    if not redirect:handlers.append(NoRedirect())
    request=urllib2.Request(url)
    if authorization is not None:request.add_header('Authorization',authorization)
    return closing(urllib2.build_opener(*handlers).open(request,timeout=3))
checks={}
try:
    probe=socket.create_connection(('127.0.0.1',1),timeout=1)
except socket.error as error:
    if error.errno!=errno.ECONNREFUSED:raise
    checks['port1']='connection_refused'
else:
    probe.close()
    raise RuntimeError('fixture port1 unexpectedly open')
import os,signal,socket,subprocess,time
function_processes=[]
def stop_function_servers(require_complete=True):
    failures=[]
    for process in function_processes:
        try:os.kill(process.pid,signal.SIGTERM)
        except OSError:pass
    deadline=time.time()+5
    while any(process.poll() is None for process in function_processes) and time.time()<deadline:time.sleep(0.05)
    for process in function_processes:
        if process.poll() is None:
            try:os.killpg(process.pid,signal.SIGKILL)
            except OSError:pass
            failures.append('forced_kill')
        process.wait()
        group_deadline=time.time()+2
        while True:
            try:os.killpg(process.pid,0)
            except OSError as error:
                if error.errno!=3:failures.append('group_state_unknown')
                break
            if time.time()>=group_deadline:
                members=[]
                for entry in os.listdir('/proc'):
                    if not entry.isdigit():continue
                    try:
                        fields=open('/proc/'+entry+'/stat').read().rsplit(')',1)[1].split()
                        if int(fields[2])==process.pid:
                            members.append({'pid':int(entry),'state':fields[0],'ppid':int(fields[1])})
                    except (IOError,ValueError,IndexError):pass
                failures.append({'group_still_present':members})
                break
            time.sleep(0.05)
    if failures or (require_complete and len(function_processes)!=2):raise RuntimeError('WSGI process cleanup unconfirmed: '+repr(failures))
try:
    for port in (80,443):
        config={'bind':'127.0.0.1:'+str(port),'workers':1,'worker_class':'gthread','threads':2,
                'keepalive':1,'timeout':10,'graceful_timeout':1,'accesslog':None,'loglevel':'warning',
                'worker_tmp_dir':'/tmp','chdir':'/tmp'}
        if port==443:
            config.update(certfile='/opt/p07-function-fixture/cert.pem',keyfile='/opt/p07-function-fixture/key.pem',
                          ssl_version=ssl.PROTOCOL_SSLv23,ciphers='AES128-SHA:AES256-SHA:ECDHE-RSA-AES128-GCM-SHA256')
        child="from __future__ import print_function\nimport os,ssl,sys\nimport gunicorn,httpbin,pkg_resources\nfrom gunicorn.app.base import BaseApplication\nfrom gunicorn.workers.gthread import ThreadWorker\nfrom gunicorn import http\nfrom email.utils import formatdate\nconfig=CONFIG\nprefix='/opt/p07-python27/lib/python2.7/site-packages/'\nfor module in (gunicorn,httpbin):\n    if not os.path.realpath(module.__file__).startswith(prefix):raise RuntimeError('foreign WSGI module')\nif gunicorn.__version__!='19.9.0' or pkg_resources.get_distribution('httpbin').version!='0.5.0':\n    raise RuntimeError('WSGI versions differ')\nif any(str(path).startswith('/testbed') for path in sys.path):raise RuntimeError('target path exposed to fixture')\nimport hashlib,json,os,re,urllib\nfrom werkzeug.http import parse_dict_header\nfixture_nonce=os.urandom(24).encode('hex')\nfixture_opaque=os.urandom(24).encode('hex')\nfixture_realm='p07-public-digest-fixture'\nmarket_destination='/press-release/jp-morgan-behauptet-sich-der-spitze-europ\\xe4ischer-anleihe-analysten-laut-umfrageergebnissen-1628202.htm'\ndef fixture_redirect(environ,start_response,destination):\n    start_response('302 Found',[('Location',destination),('Content-Length','0')])\n    return [b'']\ndef fixture_digest(environ,start_response):\n    path=environ.get('PATH_INFO','')\n    parts=path.strip('/').split('/')\n    if len(parts)!=4 or parts[0]!='digest-auth' or parts[1]!='auth':\n        start_response('404 Not Found',[('Content-Length','0')]);return [b'']\n    user,password=parts[2:]\n    header=environ.get('HTTP_AUTHORIZATION','')\n    credentials=parse_dict_header(header[7:]) if header.startswith('Digest ') else {}\n    uri=path+('?' + environ['QUERY_STRING'] if environ.get('QUERY_STRING') else '')\n    required=('username','realm','nonce','uri','response','qop','nc','cnonce','opaque')\n    valid=(all(name in credentials for name in required)\n           and credentials['username']==user and credentials['realm']==fixture_realm\n           and credentials['nonce']==fixture_nonce and credentials['opaque']==fixture_opaque\n           and credentials['uri']==uri and credentials['qop']=='auth'\n           and re.match(r'^[0-9a-fA-F]{8}$',credentials['nc']) is not None)\n    if valid:\n        h=lambda value:hashlib.md5(value.encode('utf-8') if isinstance(value,unicode) else value).hexdigest()\n        ha1=h('%s:%s:%s'%(user,fixture_realm,password))\n        ha2=h('%s:%s'%(environ['REQUEST_METHOD'],uri))\n        expected=h('%s:%s:%s:%s:auth:%s'%(ha1,fixture_nonce,credentials['nc'],credentials['cnonce'],ha2))\n        valid=credentials['response']==expected\n    if not valid:\n        challenge='Digest realm=\"%s\", nonce=\"%s\", opaque=\"%s\", qop=\"auth\", algorithm=MD5'%(fixture_realm,fixture_nonce,fixture_opaque)\n        start_response('401 Unauthorized',[('WWW-Authenticate',challenge),('Content-Length','0')])\n        return [b'']\n    body=json.dumps({'authenticated':True,'user':user}).encode('utf-8')\n    start_response('200 OK',[('Content-Type','application/json'),('Content-Length',str(len(body)))])\n    return [body]\ndef historical_application(environ,start_response):\n    host=environ.get('HTTP_HOST','').split(':',1)[0]\n    path=environ.get('PATH_INFO','')\n    environ.setdefault('CONTENT_TYPE','')\n    if environ.get('HTTP_TRANSFER_ENCODING','').lower()=='chunked':\n        environ['wsgi.input_terminated']=True\n    if host=='httpbin.org' and path.startswith('/digest-auth/'):\n        return fixture_digest(environ,start_response)\n    if host=='httpbin.org' and path=='/response-headers':\n        query=environ.get('QUERY_STRING','')\n        if '=' not in query:\n            decoded=urllib.unquote(query)\n            name,separator,value=decoded.partition(':')\n            if separator and re.match(r'^[A-Za-z0-9-]+$',name) and '\\r' not in value and '\\n' not in value:\n                environ['QUERY_STRING']=urllib.urlencode([(name,value.lstrip())])\n    if host=='www.google.com' and path=='/':environ['PATH_INFO']='/post' if environ['REQUEST_METHOD']=='POST' else '/get'\n    if host=='blip.fm' and path=='/~1abvfu':environ['PATH_INFO']='/encoding/utf8'\n    if host=='t.co' and path=='/NFrx0zLG':\n        return fixture_redirect(environ,start_response,'http://httpbin.org/get')\n    if host=='tinyurl.com' and path=='/preview.php':\n        environ['PATH_INFO']='/cookies/set/preview/0';environ['QUERY_STRING']=''\n    if host=='tinyurl.com' and path=='/7zp3jnr':\n        return fixture_redirect(environ,start_response,'http://httpbin.org/cookies')\n    if host=='feedproxy.google.com' and path=='/~r/DealipediaNews/~3/BQtUJRJeZlo/deal_view_investment.php':\n        return fixture_redirect(environ,start_response,'http://www.dealipedia.com/deal_view_investment.php?r=20012')\n    if host=='www.dealipedia.com' and path=='/deal_view_investment.php':environ['PATH_INFO']='/get'\n    if host=='www.marketwire.com' and path=='/mw/release.do':\n        return fixture_redirect(environ,start_response,'http://www.marketwire.com'+market_destination)\n    if host=='www.marketwire.com' and path.startswith('/press-release/'):\n        environ['PATH_INFO']='/encoding/utf8'\n    def fixture_response(status,headers,exc_info=None):\n        if host=='github.com' and path=='/':\n            headers=list(headers)+[('Set-Cookie','p07_fixture=public; Path=/; HttpOnly')]\n        return start_response(status,headers,exc_info)\n    return httpbin.app(environ,fixture_response)\n\nclass FixtureThreadWorker(ThreadWorker):\n    def handle_request(self,request,connection):\n        host=dict(request.headers).get('HOST','').split(':',1)[0]\n        if request.method=='HEAD' and request.path=='/NFrx0zLG' and host=='t.co':\n            connection.sock.sendall(b'HTTP/1.1 302 Found\\r\\nDate: '+formatdate(usegmt=True).encode('ascii')+b'\\r\\nLocation: http://httpbin.org/get\\r\\nTransfer-Encoding: chunked\\r\\nConnection: close\\r\\n\\r\\n')\n            return False\n        return ThreadWorker.handle_request(self,request,connection)\n    def enqueue_req(self,connection):\n        if connection.parser is None and self.cfg.is_ssl:\n            connection.sock.setblocking(True)\n            context=ssl.SSLContext(ssl.PROTOCOL_SSLv23)\n            context.options &= ~ssl.OP_NO_SSLv3\n            context.load_cert_chain(self.cfg.certfile,self.cfg.keyfile)\n            context.set_ciphers(self.cfg.ciphers)\n            connection.sock=context.wrap_socket(connection.sock,server_side=True,\n                do_handshake_on_connect=self.cfg.do_handshake_on_connect,\n                suppress_ragged_eofs=self.cfg.suppress_ragged_eofs)\n            connection.parser=http.RequestParser(self.cfg,connection.sock)\n        return ThreadWorker.enqueue_req(self,connection)\nclass FixedApplication(BaseApplication):\n    def load_config(self):\n        for key,value in config.items():self.cfg.set(key,value)\n        self.cfg.set('worker_class','__main__.FixtureThreadWorker')\n    def load(self):return historical_application\nFixedApplication().run()\n".replace('CONFIG',repr(config))
        process=subprocess.Popen(['/opt/p07-runtime/python27','-c',child],cwd='/tmp',preexec_fn=os.setsid,close_fds=True)
        function_processes.append(process)
        for attempt in range(60):
            if process.poll() is not None:raise RuntimeError('WSGI master exited during startup')
            try:
                connection=socket.create_connection(('127.0.0.1',port),0.1)
                connection.close()
                break
            except socket.error:time.sleep(0.05)
        else:raise RuntimeError('WSGI listener startup timed out')
except:
    stop_function_servers(require_complete=False)
    raise
os.environ['HTTPBIN_URL']='http://httpbin.org/'
os.environ['REQUESTS_CA_BUNDLE']='/opt/p07-function-fixture/cacert.pem'
os.environ['CURL_CA_BUNDLE']='/opt/p07-function-fixture/cacert.pem'

try:
    context=ssl.create_default_context(cafile=function_execution_ca)
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
        if (not isinstance(error.reason,ssl.SSLError)
            or ('CERTIFICATE_VERIFY_FAILED' not in str(error.reason)
                and getattr(error.reason,'reason',None)!='CERTIFICATE_VERIFY_FAILED')):
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
    connection=socket.create_connection(('t.co',80),3)
    connection.sendall(b'HEAD /NFrx0zLG HTTP/1.1\r\nHost: t.co\r\nConnection: close\r\n\r\n')
    wire=b''
    try:
        while True:
            block=connection.recv(4096)
            if not block:break
            wire+=block
            if len(wire)>16384:raise RuntimeError('chunked HEAD wire output exceeded bound')
    finally:connection.close()
    headers,separator,body=wire.partition(b'\r\n\r\n')
    lines=headers.split(b'\r\n')
    fields=dict(line.split(b': ',1) for line in lines[1:])
    if (not separator or lines[0]!=b'HTTP/1.1 302 Found' or fields.get(b'Transfer-Encoding')!=b'chunked'
        or fields.get(b'Location')!=b'http://httpbin.org/get' or b'Content-Length' in fields or body!=b''):
        raise RuntimeError('actual chunked HEAD redirect wire contract differs')
    checks['chunked_head_redirect']='real_302_chunked_header_no_HEAD_body'
    legacy=ssl.SSLContext(ssl.PROTOCOL_SSLv3)
    legacy.verify_mode=ssl.CERT_REQUIRED
    legacy.load_verify_locations(function_execution_ca)
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
    except ssl.CertificateError:
        checks['wrong_hostname']='certificate_verification_failed'
    except urllib2.URLError as error:
        if not isinstance(error.reason,ssl.CertificateError):raise
        checks['wrong_hostname']='certificate_verification_failed'
    else:raise RuntimeError('mismatched certificate hostname accepted')
except BaseException:
    stop_function_servers()
    raise
function_checks=checks
function_stopped=False
'''

_MAIN = r'''try:
    code=int(pytest.main(arguments,plugins=[evidence]))
finally:
    stop_function_servers()
    function_stopped=True
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
                  github_cookie={"status": 200, "cookie": "p07_fixture=public; Path=/; HttpOnly"},
                  http11_keepalive_close="real_persistent_connection", chunked_request="abc",
                  legacy_sslv3="verified_chain_and_hostname", wrong_hostname="certificate_verification_failed",
                  digest="real_response_hash_validated_wrong_password_rejected",
                  chunked_head_redirect="real_302_chunked_header_no_HEAD_body")
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
