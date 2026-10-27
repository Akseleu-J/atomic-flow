"""Host-owned real WSGI transport for the fixed historical Requests863 suite.

Only source generation: execution requires separate image and isolation admission.
"""
from __future__ import annotations

import json

CHILD = r'''from __future__ import print_function
import os,ssl,sys
import gunicorn,httpbin,pkg_resources
from gunicorn.app.base import BaseApplication
config=CONFIG
prefix='/opt/p07-python27/lib/python2.7/site-packages/'
for module in (gunicorn,httpbin):
    if not os.path.realpath(module.__file__).startswith(prefix):raise RuntimeError('foreign WSGI module')
if gunicorn.__version__!='19.9.0' or pkg_resources.get_distribution('httpbin').version!='0.5.0':
    raise RuntimeError('WSGI versions differ')
if any(str(path).startswith('/testbed') for path in sys.path):raise RuntimeError('target path exposed to fixture')
def application(environ,start_response):
    def fixture_response(status,headers,exc_info=None):
        if environ.get('HTTP_HOST','').split(':',1)[0]=='github.com' and environ.get('PATH_INFO')=='/':
            headers=list(headers)+[('Set-Cookie','p07_fixture=public; Path=/; HttpOnly')]
        return start_response(status,headers,exc_info)
    return httpbin.app(environ,fixture_response)
class FixedApplication(BaseApplication):
    def load_config(self):
        for key,value in config.items():self.cfg.set(key,value)
    def load(self):return application
FixedApplication().run()
'''

START = r'''import os,signal,socket,subprocess,time
function_processes=[]
def stop_function_servers():
    failures=[]
    for process in function_processes:
        try:os.killpg(process.pid,signal.SIGTERM)
        except OSError:pass
    deadline=time.time()+5
    while any(process.poll() is None for process in function_processes) and time.time()<deadline:time.sleep(0.05)
    for process in function_processes:
        if process.poll() is None:
            try:os.killpg(process.pid,signal.SIGKILL)
            except OSError:pass
            failures.append('forced_kill')
        process.wait()
        try:os.killpg(process.pid,0)
        except OSError as error:
            if error.errno!=3:failures.append('group_state_unknown')
        else:failures.append('group_still_present')
    if failures or len(function_processes)!=2:raise RuntimeError('WSGI process cleanup unconfirmed: '+repr(failures))
try:
    for port in (80,443):
        config={'bind':'127.0.0.1:'+str(port),'workers':1,'worker_class':'gthread','threads':2,
                'keepalive':1,'timeout':10,'graceful_timeout':1,'accesslog':None,'loglevel':'warning',
                'worker_tmp_dir':'/tmp','chdir':'/tmp'}
        if port==443:
            config.update(certfile='/opt/p07-function-fixture/cert.pem',keyfile='/opt/p07-function-fixture/key.pem',
                          ssl_version=ssl.PROTOCOL_SSLv23)
        child=CHILD_TEMPLATE.replace('CONFIG',repr(config))
        process=subprocess.Popen(['/opt/p07-runtime/python27','-c',child],cwd='/tmp',preexec_fn=os.setsid)
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
    stop_function_servers()
    raise
os.environ['HTTPBIN_URL']='http://httpbin.org/'
os.environ['REQUESTS_CA_BUNDLE']='/opt/p07-function-fixture/cacert.pem'
os.environ['CURL_CA_BUNDLE']='/opt/p07-function-fixture/cacert.pem'
'''

STOP = "stop_function_servers()\nprint('P07_FUNCTION_SERVERS_STOPPED=1')\nsys.stdout.flush()\n"


def server_source(certificate_files):
    expected = {"/opt/p07-function-fixture/" + name for name in ("cacert.pem", "cert.pem", "key.pem")}
    if set(certificate_files) != expected:
        raise ValueError("exact fixture certificate inventory required")
    check = r'''import hashlib,json,os,ssl,sys
for absolute,expected in json.loads(EXPECTED).items():
    if os.path.islink(absolute) or os.path.realpath(absolute)!=absolute or hashlib.sha256(open(absolute,'rb').read()).hexdigest()!=expected:
        raise RuntimeError('fixed fixture certificate bytes differ')
'''.replace("EXPECTED", repr(json.dumps(certificate_files, sort_keys=True)))
    return check + START.replace("CHILD_TEMPLATE", repr(CHILD))
