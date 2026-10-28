"""Pure launcher for the reviewed private Python3.6 native SymPy boundary.

No process is launched here. Host runtime admission must independently verify
the complete installed dependency inventory; observing mpmath below does not
replace that byte-level admission or establish independent oracle success.
"""
from __future__ import annotations

import inspect
import json
import re

from atomic_flow.check_runtime import RuntimeIdentity
from atomic_flow.check_spec import CheckSpec, SymPyRunnerIdentity

_BOOTSTRAP = r'''
import hashlib,json,os,re,runpy,sys,warnings
PARSE_SOURCE
payload=json.loads(PAYLOAD_JSON)
runtime=payload['runtime']
identity=payload['identity']
root=payload['root']
paths=payload['paths']
nonce=payload['nonce']
limit=payload['limit']
if (os.path.realpath(os.getcwd())!=root or os.path.realpath(root)!=root
        or sys.executable!=runtime['interpreter'] or sys.prefix!=runtime['private_prefix']
        or list(sys.version_info[:3])!=[3,6,15] or not sys.flags.isolated
        or not sys.flags.dont_write_bytecode or os.environ.get('PYTHONHOME')!=runtime['private_prefix']
        or any(value and not value.startswith(runtime['private_prefix']+'/') for value in sys.path)):
    raise SystemExit(125)
def checked_file(absolute,expected,maximum):
    if os.path.realpath(absolute)!=absolute or not os.path.isfile(absolute):
        raise RuntimeError('private runner path identity differs')
    with open(absolute,'rb') as stream:
        raw=stream.read(maximum+1)
    if len(raw)>maximum or hashlib.sha256(raw).hexdigest()!=expected:
        raise RuntimeError('private runner byte identity differs')
def protected():
    checked_file(runtime['launcher'],runtime['launcher_sha256'],65536)
    for relative,expected in identity['source_sha256'].items():
        checked_file(os.path.join(root,relative),expected,4*1024*1024)
protected()
entries=0
for directory,dirs,files in os.walk(root,followlinks=False):
    for name in dirs+files:
        entries+=1
        absolute=os.path.join(directory,name)
        if entries>100000 or os.path.islink(absolute) or name.endswith(('.pyc','.pyo')):
            raise SystemExit(125)
sys.dont_write_bytecode=True
warnings.filterwarnings('ignore',category=UserWarning)
warnings.filterwarnings('ignore',category=SyntaxWarning)
sys.path[:0]=[os.path.join(root,'bin'),root]
import mpmath,sympy
module=os.path.realpath(sympy.__file__)
mpmath_module=os.path.realpath(mpmath.__file__)
if (module!=os.path.join(root,'sympy','__init__.py') or sympy.__version__!=identity['sympy_version']
        or mpmath.__version__!='1.2.1'
        or not mpmath_module.startswith(runtime['private_prefix']+'/lib/python3.6/site-packages/mpmath/')):
    raise SystemExit(125)
with open(mpmath_module,'rb') as stream:
    mpmath_raw=stream.read(4*1024*1024+1)
if len(mpmath_raw)>4*1024*1024:
    raise SystemExit(125)
class BoundedText:
    encoding='utf-8'
    errors='strict'
    def __init__(self):
        self.parts=[]
        self.size=0
        self.overflow=False
    def write(self,text):
        if not isinstance(text,str):
            raise TypeError('native output must be text')
        if not text:
            return 0
        size=len(text.encode('utf-8'))
        if self.size+size>limit:
            self.overflow=True
            raise RuntimeError('native output exceeded admitted byte limit')
        self.parts.append(text)
        self.size+=size
        return len(text)
    def flush(self):
        pass
    def isatty(self):
        return False
    def value(self):
        return ''.join(self.parts)
stdout,stderr=BoundedText(),BoundedText()
original_stdout,original_stderr=sys.stdout,sys.stderr
original_argv=sys.argv
native=[os.path.join(root,'bin','test'),'-C','--verbose']+paths
code=125
try:
    sys.stdout,sys.stderr=stdout,stderr
    sys.argv=list(native)
    try:
        runpy.run_path(native[0],run_name='__main__')
    except SystemExit as error:
        code=error.code if type(error.code) is int else 0 if error.code is None else 125
    else:
        code=0
finally:
    sys.stdout,sys.stderr=original_stdout,original_stderr
    sys.argv=original_argv
    protected()
if stdout.overflow or stderr.overflow or code not in (0,1):
    raise SystemExit(125)
body=stdout.value()
errors=stderr.value()
if not body.endswith('\n'):
    body+='\n'
summary=_parse_sympy_output(body,paths,code)
receipt={'nonce':nonce,'root':root,'interpreter':sys.executable,
         'interpreter_resolved':os.path.realpath(sys.executable),'runner':'sympy-native',
         'sympy_module':module,'sympy_version':sympy.__version__,'python_version':list(sys.version_info[:3]),
         'native_source_sha256':identity['source_sha256'],'native_argv':native,
         'finished':True,'exit_code':code,'native_stdout_sha256':hashlib.sha256(body.encode('utf-8')).hexdigest(),
         'runtime_digest':payload['runtime_digest'],'check_spec_digest':payload['check_spec_digest'],
         'source_revision':payload['source_revision'],'launcher_sha256':runtime['launcher_sha256'],
         'mpmath_module':mpmath_module,'mpmath_version':mpmath.__version__,
         'mpmath_module_sha256':hashlib.sha256(mpmath_raw).hexdigest(),
         'dependency_inventory_sha256':runtime['inventory_sha256'],
         'dependency_inventory_verification':'required_separately_by_host_admission',
         'warning_policy':['ignore::UserWarning','ignore::SyntaxWarning']}
receipt.update(summary)
sys.stderr.write(errors)
print('ATOMIC_SYMPY_BEGIN:'+nonce,flush=True)
sys.stdout.write(body)
print('ATOMIC_SYMPY_END:'+nonce,flush=True)
print('ATOMIC_CHECK_SPEC_RECEIPT:'+json.dumps(receipt,sort_keys=True),flush=True)
complete=code==0 and not any(summary['counts'][name] for name in ('failed','exceptions','skipped','xpassed'))
raise SystemExit(code if code else 0 if complete else 5)
'''


def private_sympy_args(spec: CheckSpec, nonce: str) -> list[str]:
    """Build fixed private-launcher -c arguments; no subprocess or target import."""
    if (type(spec) is not CheckSpec or type(nonce) is not str
            or re.fullmatch(r"[0-9a-f]{32}", nonce) is None):
        raise ValueError("immutable native spec and fresh host nonce required")
    runtime = spec.runtime_identity
    identity = spec.sympy_identity
    if (type(runtime) is not RuntimeIdentity or type(identity) is not SymPyRunnerIdentity
            or runtime.python_version != (3, 6, 15) or identity.python_version != runtime.python_version
            or runtime.runner != "sympy-native" or spec.runner != "sympy-native"
            or spec.interpreter != runtime.interpreter or spec.image != runtime.image_id):
        raise ValueError("private native SymPy requires the exact admitted Python3.6.15 runtime and runner")
    runtime.require_execution_eligible()
    from atomic_flow.check_execution import _parse_sympy_output

    payload = {"runtime": runtime.as_dict(), "runtime_digest": runtime.digest,
               "identity": identity.as_dict(), "check_spec_digest": spec.digest,
               "source_revision": spec.source_revision, "nonce": nonce,
               "root": spec.target_root, "paths": list(spec.test_paths),
               "limit": spec.resources.output_bytes_per_stream}
    source = _BOOTSTRAP.replace("PARSE_SOURCE", inspect.getsource(_parse_sympy_output))
    source = source.replace("PAYLOAD_JSON", repr(json.dumps(payload, sort_keys=True, ensure_ascii=True)))
    return ["-c", source]
