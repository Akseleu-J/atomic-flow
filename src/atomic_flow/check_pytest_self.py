"""Host-owned pytest repository self-check launcher and completion contract.

This module does not admit a task, provision an image, execute a process or
attest target code. Callers must bind this identity to their admitted immutable
image/interpreter and actual source revision, using the existing Docker checks
profile. Target tests can still lie inside their own interpreter.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from atomic_flow.agents.execution import CommandResult

MARKER = "ATOMIC_CHECK_SPEC_RECEIPT:"
_CORE = ("__init__.py",)


@dataclass(frozen=True, slots=True)
class PytestSelfIdentity:
    """Pinned bootstrap machinery; edited implementation files remain editable.

    The source hashes must come from the accepted task base, never from model
    output. Extra protected files may be added by the host admission registry.
    """

    python_version: tuple[int, int, int]
    pytest_version: str
    source_sha256: tuple[tuple[str, str], ...]
    source_root: str = "src"
    pytest_entrypoint: str = "src/pytest.py"
    traceback: str = "auto"

    def __post_init__(self) -> None:
        if (type(self.python_version) is not tuple or len(self.python_version) != 3
                or any(type(v) is not int or not 0 <= v <= 999 for v in self.python_version)
                or self.python_version[0] != 3 or self.python_version[1] < 8):
            raise ValueError("pytest self checks require exact Python 3.8+ identity")
        if (type(self.pytest_version) is not str
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}", self.pytest_version) is None):
            raise ValueError("exact pytest version required")
        if type(self.source_root) is not str or self.source_root not in {"src", ""}:
            raise ValueError("unsupported pytest source layout")
        prefix = self.source_root + "/" if self.source_root else ""
        if (type(self.pytest_entrypoint) is not str
                or self.pytest_entrypoint not in {prefix + "pytest.py", prefix + "pytest/__init__.py"}):
            raise ValueError("pytest entrypoint differs from source layout")
        if type(self.traceback) is not str or self.traceback not in {"auto", "short"}:
            raise ValueError("unreviewed traceback profile")
        if (type(self.source_sha256) is not tuple or not 2 <= len(self.source_sha256) <= 128
                or any(type(pair) is not tuple or len(pair) != 2 for pair in self.source_sha256)):
            raise ValueError("immutable protected runner inventory required")
        paths = []
        for path, digest in self.source_sha256:
            if (type(path) is not str or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,511}", path) is None
                    or any(part in {"", ".", ".."} for part in path.split("/"))
                    or type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
                raise ValueError("invalid protected runner path or digest")
            paths.append(path)
        required = {self.pytest_entrypoint, *(prefix + "_pytest/" + path for path in _CORE)}
        if paths != sorted(set(paths)) or not required <= set(paths):
            raise ValueError("complete sorted protected pytest runner inventory required")

    def as_dict(self) -> dict:
        return {"python_version": list(self.python_version), "pytest_version": self.pytest_version,
                "source_sha256": dict(self.source_sha256), "source_root": self.source_root,
                "pytest_entrypoint": self.pytest_entrypoint, "traceback": self.traceback}


_BOOTSTRAP = r'''
import hashlib,json,os,sys,tempfile
nonce,root,interpreter,encoded=sys.argv[1:5]
paths=sys.argv[5:]
identity=json.loads(encoded)
if (not sys.flags.isolated or os.path.realpath(os.getcwd())!=root
        or sys.executable!=interpreter or list(sys.version_info[:3])!=identity['python_version']):
    raise SystemExit(125)
def protected():
    for relative,expected in identity['source_sha256'].items():
        absolute=os.path.join(root,relative)
        if os.path.realpath(absolute)!=absolute or not os.path.isfile(absolute):
            raise SystemExit(125)
        with open(absolute,'rb') as stream:
            raw=stream.read(4*1024*1024+1)
        if len(raw)>4*1024*1024 or hashlib.sha256(raw).hexdigest()!=expected:
            raise SystemExit(125)
protected()
sys.dont_write_bytecode=True
sys.pycache_prefix=tempfile.mkdtemp(prefix='atomic-pytest-self-bytecode-')
sys.path.insert(0,os.path.join(root,identity['source_root']))
import pytest,_pytest
module=os.path.realpath(pytest.__file__)
internal=os.path.realpath(_pytest.__file__)
if (module!=os.path.join(root,identity['pytest_entrypoint'])
        or internal!=os.path.join(root,identity['source_root'],'_pytest','__init__.py')
        or pytest.__version__!=identity['pytest_version']):
    raise SystemExit(125)
class Evidence:
    def __init__(self):
        self.collected=0;self.files=set();self.skipped=0;self.failed=0
        self.call_failed=0;self.setup_failed=0;self.teardown_failed=0
        self.collection_errors=0;self.finished=False;self.completed=set()
        self.collection_complete=False
    def pytest_collection_finish(self,session):
        self.collection_complete=True
        self.collected=len(session.items)
        for item in session.items:
            name=str(getattr(item,'path',None) or item.fspath)
            self.files.add(os.path.relpath(os.path.realpath(name),root).replace(os.sep,'/'))
    def pytest_collectreport(self,report):
        if report.failed:self.collection_errors+=1
    def pytest_runtest_logreport(self,report):
        if report.when=='teardown':self.completed.add(report.nodeid)
        if report.skipped:self.skipped+=1
        if report.failed:
            self.failed+=1
            if report.when=='call':self.call_failed+=1
            elif report.when=='setup':self.setup_failed+=1
            elif report.when=='teardown':self.teardown_failed+=1
    def pytest_sessionfinish(self,session,exitstatus):self.finished=True
evidence=Evidence()
arguments=['-rA']
if identity['traceback']=='short':arguments.append('--tb=short')
code=int(pytest.main(arguments+paths,plugins=[evidence]))
protected()
receipt={'nonce':nonce,'root':root,'runner':'pytest-self','interpreter':sys.executable,
         'interpreter_resolved':os.path.realpath(sys.executable),'python_version':list(sys.version_info[:3]),
         'pytest_module':module,'_pytest_module':internal,'pytest_version':pytest.__version__,
         'pytest_self_identity':identity,'argv':arguments+paths,
         'collected':evidence.collected,'files':sorted(evidence.files),'finished':evidence.finished,
         'collection_complete':evidence.collection_complete,'collection_errors':evidence.collection_errors,
         'completed':len(evidence.completed),'skipped':evidence.skipped,'failed':evidence.failed,
         'call_failed':evidence.call_failed,'setup_failed':evidence.setup_failed,
         'teardown_failed':evidence.teardown_failed,'exit_code':code}
print('\nATOMIC_CHECK_SPEC_RECEIPT:'+json.dumps(receipt,sort_keys=True),flush=True)
complete=(evidence.finished and evidence.collection_complete and evidence.collected>0
          and len(evidence.completed)==evidence.collected and evidence.files==set(paths)
          and not evidence.collection_errors and not evidence.skipped
          and not evidence.setup_failed and not evidence.teardown_failed)
raise SystemExit(code if code else 0 if complete and not evidence.failed else 5)
'''


def pytest_self_args(identity: PytestSelfIdentity, nonce: str, target_root: str,
                     interpreter: str, test_paths: tuple[str, ...]) -> list[str]:
    """Build isolated interpreter arguments only; never starts target code."""
    if (type(identity) is not PytestSelfIdentity or type(nonce) is not str
            or re.fullmatch(r"[0-9a-f]{32}", nonce) is None):
        raise ValueError("immutable runner identity and fresh nonce required")
    if type(target_root) is not str or type(interpreter) is not str or target_root not in {"/workspace", "/testbed"} or interpreter not in {
            "/opt/miniconda3/envs/testbed/bin/python", "/usr/local/bin/python", "/usr/bin/python3"}:
        raise ValueError("unreviewed runtime paths")
    if (type(test_paths) is not tuple or not 1 <= len(test_paths) <= 256
            or any(type(path) is not str for path in test_paths) or len(set(test_paths)) != len(test_paths)):
        raise ValueError("complete immutable ordinary test inventory required")
    for path in test_paths:
        if (type(path) is not str or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,511}\.py", path) is None
                or any(part in {"", ".", ".."} or part.startswith((".", "-")) for part in path.split("/"))):
            raise ValueError("ordinary Python test file required")
    return ["-I", "-c", _BOOTSTRAP, nonce, target_root, interpreter,
            json.dumps(identity.as_dict(), sort_keys=True), *test_paths]


def pytest_self_receipt(result: CommandResult, identity: PytestSelfIdentity, nonce: str,
                        target_root: str, interpreter: str, test_paths: tuple[str, ...]) -> dict | None:
    """Accept complete PASS or ordinary call FAIL; infrastructure never repairs."""
    pytest_self_args(identity, nonce, target_root, interpreter, test_paths)
    if (type(result.returncode) is not int or result.returncode not in {0, 1} or result.timed_out
            or result.stdout_truncated or result.stderr_truncated):
        return None
    lines = [line[len(MARKER):] for line in result.stdout.splitlines() if line.startswith(MARKER)]
    if len(lines) != 1:
        return None
    try:
        value = json.loads(lines[0])
    except (TypeError, ValueError):
        return None
    prefix = target_root + ("/" + identity.source_root if identity.source_root else "")
    arguments = ["-rA"] + (["--tb=short"] if identity.traceback == "short" else []) + list(test_paths)
    expected = {"nonce": nonce, "root": target_root, "runner": "pytest-self", "interpreter": interpreter,
                "python_version": list(identity.python_version), "pytest_version": identity.pytest_version,
                "pytest_module": target_root + "/" + identity.pytest_entrypoint,
                "_pytest_module": prefix + "/_pytest/__init__.py", "pytest_self_identity": identity.as_dict(),
                "argv": arguments, "files": sorted(test_paths)}
    if type(value) is not dict or any(value.get(key) != item for key, item in expected.items()):
        return None
    resolved = value.get("interpreter_resolved")
    if (type(resolved) is not str or not resolved.startswith("/")
            or any(part in {"", ".", ".."} for part in resolved[1:].split("/"))):
        return None
    counters = ("collected", "completed", "collection_errors", "skipped", "failed", "call_failed", "setup_failed", "teardown_failed", "exit_code")
    if any(type(value.get(key)) is not int or value[key] < 0 for key in counters):
        return None
    if (value.get("finished") is not True or value.get("collection_complete") is not True
            or value["collected"] <= 0 or value["completed"] != value["collected"]
            or any(value[key] for key in ("collection_errors", "skipped", "setup_failed", "teardown_failed"))
            or value["exit_code"] != result.returncode or value["failed"] != value["call_failed"]
            or value["call_failed"] > value["collected"]
            or bool(value["call_failed"]) != (result.returncode == 1)):
        return None
    return value
