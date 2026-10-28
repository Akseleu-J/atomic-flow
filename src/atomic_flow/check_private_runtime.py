"""Pure target-grammar syntax requests for reviewed private Python runtimes.

This module never launches a process or admits an image. The host must verify
runtime evidence, snapshot identity and bounded Docker lifecycle separately.
Syntax observations are not ordinary-test receipts or execution permission.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from atomic_flow.check_runtime import RuntimeIdentity

if TYPE_CHECKING:
    from atomic_flow.agents.execution import CommandResult

SYNTAX_MARKER = "ATOMIC_PRIVATE_SYNTAX_RECEIPT:"
SYNTAX_POLICY = "private-target-grammar/1"
MAX_SOURCE_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class PrivateSyntaxRequest:
    runtime: RuntimeIdentity
    check_spec_digest: str
    source_revision: str
    path: str
    file_sha256: str
    nonce: str
    target_root: str = "/workspace"

    def __post_init__(self) -> None:
        if type(self.runtime) is not RuntimeIdentity:
            raise ValueError("immutable host runtime identity required")
        self.runtime.require_execution_eligible()
        for name in ("check_spec_digest", "source_revision", "file_sha256"):
            value = getattr(self, name)
            if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(name + " must be an exact SHA256 digest")
        if type(self.nonce) is not str or re.fullmatch(r"[0-9a-f]{32}", self.nonce) is None:
            raise ValueError("fresh host nonce required")
        if (type(self.path) is not str or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,511}\.pyi?", self.path) is None
                or any(part in {"", ".", ".."} or part.startswith((".", "-")) for part in self.path.split("/"))):
            raise ValueError("syntax path must be a confined ordinary Python file")
        if type(self.target_root) is not str or self.target_root not in {"/workspace", "/testbed"}:
            raise ValueError("unreviewed source root")

    def as_dict(self) -> dict:
        return {"schema_version": 1, "kind": "host_private_syntax_request", "policy": SYNTAX_POLICY,
                "runtime": self.runtime.as_dict(), "runtime_digest": self.runtime.digest,
                "check_spec_digest": self.check_spec_digest, "source_revision": self.source_revision,
                "path": self.path, "file_sha256": self.file_sha256, "nonce": self.nonce,
                "target_root": self.target_root, "max_source_bytes": MAX_SOURCE_BYTES}

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


# Compatible with Python 2.7 and 3.6. No target module imports or source exec.
# The fixed Python2 launcher removes positional argv; embed only validated JSON.
_SYNTAX_BOOTSTRAP = r'''
from __future__ import print_function
import hashlib,json,os,sys
request=json.loads(REQUEST_JSON)
runtime=request['runtime']
root=request['target_root']
if (os.path.realpath(os.getcwd())!=root or os.path.realpath(root)!=root
        or sys.executable!=runtime['interpreter'] or sys.prefix!=runtime['private_prefix']
        or list(sys.version_info[:3])!=runtime['python_version']):
    raise SystemExit(125)
if sys.version_info[0]==2:
    if not (sys.flags.ignore_environment and sys.flags.no_user_site and sys.flags.no_site):
        raise SystemExit(125)
    expected=[runtime['private_prefix']+'/lib/python27.zip',runtime['private_prefix']+'/lib/python2.7',
              runtime['private_prefix']+'/lib/python2.7/lib-dynload',runtime['private_prefix']+'/lib/python2.7/site-packages']
    if sys.path!=expected:
        raise SystemExit(125)
else:
    if not sys.flags.isolated or os.environ.get('PYTHONHOME')!=runtime['private_prefix']:
        raise SystemExit(125)
    if any(value and not value.startswith(runtime['private_prefix']+'/') for value in sys.path):
        raise SystemExit(125)
if not sys.flags.dont_write_bytecode:
    raise SystemExit(125)
launcher=runtime['launcher']
if os.path.realpath(launcher)!=launcher or not os.path.isfile(launcher):
    raise SystemExit(125)
with open(launcher,'rb') as stream:
    launcher_raw=stream.read(65537)
if len(launcher_raw)>65536 or hashlib.sha256(launcher_raw).hexdigest()!=runtime['launcher_sha256']:
    raise SystemExit(125)
absolute=os.path.join(root,request['path'])
if os.path.realpath(absolute)!=absolute or not os.path.isfile(absolute):
    raise SystemExit(125)
with open(absolute,'rb') as stream:
    raw=stream.read(request['max_source_bytes']+1)
if len(raw)>request['max_source_bytes'] or hashlib.sha256(raw).hexdigest()!=request['file_sha256']:
    raise SystemExit(125)
outcome='passed'
diagnostic=None
try:
    compile(raw,request['path'],'exec',0,True)
except SyntaxError as error:
    outcome='failed'
    diagnostic={'kind':'SyntaxError','line':error.lineno or 0,'offset':error.offset or 0}
with open(absolute,'rb') as stream:
    after=stream.read(request['max_source_bytes']+1)
if after!=raw:
    raise SystemExit(125)
receipt={'schema_version':1,'kind':'private_syntax_observation','policy':request['policy'],
         'nonce':request['nonce'],'request_digest':REQUEST_DIGEST,'runtime_digest':request['runtime_digest'],
         'check_spec_digest':request['check_spec_digest'],'source_revision':request['source_revision'],
         'path':request['path'],'file_sha256':request['file_sha256'],'root':root,
         'interpreter':sys.executable,'python_version':list(sys.version_info[:3]),
         'prefix':sys.prefix,'outcome':outcome,'diagnostic':diagnostic,'finished':True}
print('ATOMIC_PRIVATE_SYNTAX_RECEIPT:'+json.dumps(receipt,sort_keys=True))
sys.stdout.flush()
raise SystemExit(0 if outcome=='passed' else 1)
'''


def private_syntax_args(request: PrivateSyntaxRequest) -> tuple[str, str]:
    """Arguments for the exact reviewed private launcher; never executes it."""
    if type(request) is not PrivateSyntaxRequest:
        raise ValueError("immutable syntax request required")
    # Replace digest first so payload content cannot become a template token.
    source = _SYNTAX_BOOTSTRAP.replace("REQUEST_DIGEST", repr(request.digest))
    source = source.replace("REQUEST_JSON", repr(request.canonical_bytes().decode("ascii")))
    return "-c", source


def private_syntax_receipt(result: CommandResult, request: PrivateSyntaxRequest) -> dict | None:
    """Validate only completed syntax pass/failure; infrastructure returns None."""
    from atomic_flow.agents.execution import CommandResult

    if type(request) is not PrivateSyntaxRequest or type(result) is not CommandResult:
        return None
    if (type(result.returncode) is not int or result.returncode not in {0, 1}
            or result.timed_out or result.stdout_truncated or result.stderr_truncated
            or type(result.stdout) is not str or type(result.stderr) is not str
            or len(result.stdout.encode("utf-8")) > 16_384 or len(result.stderr.encode("utf-8")) > 16_384):
        return None
    lines = result.stdout.splitlines()
    if len(lines) != 1 or not lines[0].startswith(SYNTAX_MARKER) or result.stderr.strip():
        return None
    try:
        value = json.loads(lines[0][len(SYNTAX_MARKER):])
    except (TypeError, ValueError):
        return None
    expected = {"schema_version": 1, "kind": "private_syntax_observation", "policy": SYNTAX_POLICY,
                "nonce": request.nonce, "request_digest": request.digest, "runtime_digest": request.runtime.digest,
                "check_spec_digest": request.check_spec_digest, "source_revision": request.source_revision,
                "path": request.path, "file_sha256": request.file_sha256, "root": request.target_root,
                "interpreter": request.runtime.interpreter, "python_version": list(request.runtime.python_version),
                "prefix": request.runtime.private_prefix, "outcome": "passed" if result.returncode == 0 else "failed",
                "finished": True}
    if (type(value) is not dict or set(value) != set(expected) | {"diagnostic"}
            or any(type(value.get(key)) is not type(item) or value[key] != item for key, item in expected.items())):
        return None
    if any(type(part) is not int for part in value["python_version"]):
        return None
    diagnostic = value["diagnostic"]
    if result.returncode == 0:
        return value if diagnostic is None else None
    if (type(diagnostic) is not dict or set(diagnostic) != {"kind", "line", "offset"}
            or diagnostic["kind"] != "SyntaxError"
            or any(type(diagnostic.get(key)) is not int or not 0 <= diagnostic[key] <= MAX_SOURCE_BYTES
                   for key in ("line", "offset"))):
        return None
    return value
