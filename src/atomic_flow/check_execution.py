"""Execute admitted installed-pytest, pytest-self or native-SymPy Docker checks.

TaskRunner's host CheckObligation uses this boundary for tool, edit, step and
final checks. A target process can lie about its own pytest hooks/output; its
receipt is not an independent oracle grade or remote attestation. Native SymPy
requires a separately admitted exact runner identity and complete counted output.
Pytest repository self-tests use a separate pinned entrypoint identity and
import the actual edited source root inside the same bounded Docker lifecycle.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from atomic_flow.agents.execution import CommandResult, ExecutionUnavailable
from atomic_flow.check_pytest_self import pytest_self_args, pytest_self_receipt
from atomic_flow.check_spec import CheckSpec
from atomic_flow.config import ExecutionConfig

_MARKER = "ATOMIC_CHECK_SPEC_RECEIPT:"
_BOOTSTRAP = r'''
import json,os,sys,tempfile
import pytest
nonce,expected_root = sys.argv[1:3]
paths = sys.argv[3:]
if sys.version_info < (3,8):
    raise SystemExit(125)
root = os.path.realpath(os.getcwd())
if root != expected_root:
    raise SystemExit(125)
runner_path = os.path.realpath(pytest.__file__)
if runner_path == root or runner_path.startswith(root + os.sep):
    raise SystemExit(125)
sys.pycache_prefix = tempfile.mkdtemp(prefix='atomic-spec-bytecode-')
sys.dont_write_bytecode = True
sys.path.insert(0, root)
class Evidence:
    collected = 0
    files = set()
    skipped = 0
    failed = 0
    call_failed = 0
    setup_failed = 0
    teardown_failed = 0
    finished = False
    def pytest_collection_finish(self,session):
        self.collected = len(session.items)
        for item in session.items:
            filename = str(getattr(item,'path',None) or item.fspath)
            relative = os.path.relpath(os.path.realpath(filename),root).replace(os.sep,'/')
            self.files.add(relative)
    def pytest_runtest_logreport(self,report):
        if report.skipped:
            self.skipped += 1
        if report.failed:
            self.failed += 1
            if report.when == 'call':self.call_failed += 1
            elif report.when == 'setup':self.setup_failed += 1
            elif report.when == 'teardown':self.teardown_failed += 1
    def pytest_sessionfinish(self,session,exitstatus):
        self.finished = True
evidence = Evidence()
code = int(pytest.main(['-q','--no-header','-x',*paths],plugins=[evidence]))
receipt = {'nonce':nonce,'root':root,'interpreter':sys.executable,
           'interpreter_resolved':os.path.realpath(sys.executable),
           'pytest_module':runner_path,'pytest_version':getattr(pytest,'__version__','unknown'),
           'collected':evidence.collected,'files':sorted(evidence.files),
           'skipped':evidence.skipped,'failed':evidence.failed,'finished':evidence.finished,
           'call_failed':evidence.call_failed,'setup_failed':evidence.setup_failed,'teardown_failed':evidence.teardown_failed,
           'exit_code':code}
print('\nATOMIC_CHECK_SPEC_RECEIPT:' + json.dumps(receipt,sort_keys=True),flush=True)
complete = (evidence.finished and evidence.collected > 0 and evidence.files == set(paths)
            and evidence.skipped == 0 and evidence.failed == 0)
raise SystemExit(0 if code == 0 and complete else code if code else 5)
'''


def _parse_sympy_output(text, paths, exit_code):
    """Count the actual native verbose stream; unsupported/partial shapes refuse.

    This is presentation integrity, not proof that hostile target code told the
    truth. Native expected failures remain expected failures, never ordinary PASS.
    """
    if not isinstance(text, str) or type(exit_code) is not int or exit_code not in {0, 1}:
        raise ValueError("native output or exit identity differs")
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    if clean.count("tests finished:") != 1:
        raise ValueError("one complete native terminal summary required")
    footer = re.search(r"(?:^|\n)\s*=*\s*tests finished:\s*(.*?)\bin ([0-9]+(?:\.[0-9]+)?) seconds\s*=*\s*(?:\n|$)", clean, re.S)
    if footer is None or len(footer.group(1)) > 2048:
        raise ValueError("native terminal summary shape differs")
    trailer = clean[footer.end():].strip()
    if trailer not in {"", "DO *NOT* COMMIT!"}:
        raise ValueError("unexpected output after native terminal summary")
    counts = {name: 0 for name in ("passed", "failed", "skipped", "xfailed", "xpassed", "exceptions")}
    names = {"passed": "passed", "failed": "failed", "skipped": "skipped", "expected to fail": "xfailed",
             "expected to fail but passed": "xpassed", "exceptions": "exceptions"}
    seen = set()
    for part in footer.group(1).split(","):
        part = " ".join(part.split())
        if not part:
            continue
        match = re.fullmatch(r"([0-9]+) (passed|failed|skipped|expected to fail|expected to fail but passed|exceptions)", part)
        if match is None or match[2] in seen:
            raise ValueError("native terminal counts are ambiguous")
        seen.add(match[2])
        counts[names[match[2]]] = int(match[1])
    if "passed" not in seen:
        raise ValueError("native terminal summary lacks passed count")
    files, tests, active, declared = {}, {}, None, 0
    categories = {"ok": "passed", "F": "failed", "E": "exceptions", "f": "xfailed", "X": "xpassed", "s": "skipped"}
    for line in clean[:footer.start()].splitlines():
        header = re.fullmatch(r"(sympy/[A-Za-z0-9_./-]+\.py)\[([0-9]+)\]\s*", line)
        if header:
            if active is not None or header[1] in files or header[1] not in paths or int(header[2]) <= 0:
                raise ValueError("native file collection differs or is incomplete")
            active, declared = header[1], int(header[2])
            files[active] = []
            continue
        status = re.fullmatch(r"(test[A-Za-z0-9_]+)\s+(ok|F|E|f|X|s)(?:\s+\[(OK|FAIL)\])?\s*", line)
        if status is None:
            # Native verbose skip output includes a human reason before the
            # status token. Preserve its skipped category, never infer PASS.
            status = re.fullmatch(r"(test[A-Za-z0-9_]+)\s+[^\r\n]{1,500}\s+(s)(?:\s+\[(OK|FAIL)\])?\s*", line)
        if status:
            if active is None or status[1] in files[active]:
                raise ValueError("native testcase lacks a unique file identity")
            files[active].append(status[1])
            tests[active + "::" + status[1]] = categories[status[2]]
            if status[3] is not None:
                if len(files[active]) != declared:
                    raise ValueError("native testcase count differs from file collection")
                file_failed = any(tests[active + "::" + name] in {"failed", "exceptions", "xpassed"} for name in files[active])
                if status[3] != ("FAIL" if file_failed else "OK"):
                    raise ValueError("native file verdict contradicts testcase observations")
                active = None
            continue
        if active is not None and line.strip():
            raise ValueError("unsupported native verbose testcase shape")
    if active is not None or set(files) != set(paths) or not tests:
        raise ValueError("native collection is empty, partial or unclosed")
    actual = {name: list(tests.values()).count(name) for name in counts}
    if actual != counts:
        raise ValueError("native test identities disagree with aggregate counts")
    if trailer and not (counts["failed"] or counts["exceptions"] or counts["xpassed"]):
        raise ValueError("native failure trailer contradicts successful testcase counts")
    if exit_code == 0 and (counts["failed"] or counts["exceptions"]):
        raise ValueError("native zero exit disagrees with failed tests")
    return {"collection": sorted(tests), "test_statuses": dict(sorted(tests.items())),
            "files": sorted(files), "collected": len(tests), "counts": counts}


_SYMPY_BOOTSTRAP = r'''
import hashlib,json,os,re,selectors,subprocess,sys,tempfile
PARSE_SOURCE
nonce,root,identity_json,limit_text=sys.argv[1:5]
paths=sys.argv[5:]
identity=json.loads(identity_json)
limit=int(limit_text)
if os.path.realpath(os.getcwd())!=root or list(sys.version_info[:3])!=identity['python_version'] or not sys.flags.isolated:
    raise SystemExit(125)
for relative,expected in identity['source_sha256'].items():
    absolute=os.path.join(root,relative)
    if os.path.islink(absolute) or os.path.realpath(absolute)!=absolute or hashlib.sha256(open(absolute,'rb').read()).hexdigest()!=expected:
        raise SystemExit(125)
sys.dont_write_bytecode=True
sys.pycache_prefix=tempfile.mkdtemp(prefix='atomic-native-bytecode-')
sys.path.insert(0,root)
import sympy
module=os.path.realpath(sympy.__file__)
if module!=os.path.join(root,'sympy','__init__.py') or sympy.__version__!=identity['sympy_version']:
    raise SystemExit(125)
native=[sys.executable,'-B',os.path.join(root,'bin','test'),'-C','--verbose']+paths
environment={key:value for key,value in os.environ.items() if key in ('PATH','HOME','LANG','LC_ALL','PYTHONDONTWRITEBYTECODE')}
environment['PYTHONPYCACHEPREFIX']=sys.pycache_prefix
environment['PYTHONDONTWRITEBYTECODE']='1'
process=subprocess.Popen(native,cwd=root,env=environment,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
streams=selectors.DefaultSelector()
streams.register(process.stdout,selectors.EVENT_READ,'stdout')
streams.register(process.stderr,selectors.EVENT_READ,'stderr')
captured={'stdout':bytearray(),'stderr':bytearray()}
try:
    while streams.get_map():
        for key,_ in streams.select(0.1):
            block=os.read(key.fileobj.fileno(),65536)
            if not block:
                streams.unregister(key.fileobj)
                key.fileobj.close()
                continue
            captured[key.data].extend(block)
            if len(captured[key.data])>limit:
                process.kill()
                raise RuntimeError('native output exceeded admitted byte limit')
    code=process.wait()
finally:
    streams.close()
    if process.poll() is None:
        process.kill()
        process.wait()
body=bytes(captured['stdout']).decode('utf-8')
errors=bytes(captured['stderr']).decode('utf-8')
sys.stderr.write(errors)
print('ATOMIC_SYMPY_BEGIN:'+nonce,flush=True)
sys.stdout.write(body)
if not body.endswith('\n'):sys.stdout.write('\n')
print('ATOMIC_SYMPY_END:'+nonce,flush=True)
summary=_parse_sympy_output(body,paths,code)
receipt={'nonce':nonce,'root':root,'interpreter':sys.executable,'interpreter_resolved':os.path.realpath(sys.executable),
         'runner':'sympy-native','sympy_module':module,'sympy_version':sympy.__version__,
         'python_version':list(sys.version_info[:3]),'native_source_sha256':identity['source_sha256'],
         'native_argv':native[2:],'finished':True,'exit_code':code,'native_stdout_sha256':hashlib.sha256(body.encode()).hexdigest(),
         **summary}
print('ATOMIC_CHECK_SPEC_RECEIPT:'+json.dumps(receipt,sort_keys=True),flush=True)
complete=code==0 and not any(summary['counts'][name] for name in ('failed','exceptions','skipped','xpassed'))
raise SystemExit(code if code else 0 if complete else 5)
'''


def admit_check_spec(spec: CheckSpec, workspace: Path, config: ExecutionConfig,
                     timeout: float, args: list[str], *, cancel_check=None) -> str:
    """Validate host inputs before any subprocess; validation is not provenance."""
    if type(spec) is not CheckSpec:
        raise ExecutionUnavailable("An immutable host-owned CheckSpec is required")
    check = cancel_check or (lambda: None)
    check()
    if spec.runner == "sympy-native" and spec.sympy_identity is None:
        raise ExecutionUnavailable("Native SymPy execution without an admitted runner identity is not implemented")
    if spec.runner == "pytest-self" and spec.pytest_self_identity is None:
        raise ExecutionUnavailable("Pytest repository self-checks require an admitted runner identity")
    if spec.task_id.startswith("pytest-dev__pytest-") and (spec.runner != "pytest-self" or spec.pytest_self_identity is None):
        raise ExecutionUnavailable("Historical pytest repository self-checks require a separate runner identity contract")
    if (config.mode != "docker" or config.profile != "checks" or config.image != spec.image
            or config.output_bytes > spec.resources.output_bytes_per_stream
            or type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 0 < timeout <= spec.timeout_seconds or args != list(spec.argv[1:])):
        raise ExecutionUnavailable("Executor, arguments or bounds differ from the host CheckSpec")
    root = workspace.absolute()
    for path in (root, *root.parents):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ExecutionUnavailable("CheckSpec workspace contains a link")
    if not root.is_dir():
        raise ExecutionUnavailable("CheckSpec requires an admitted workspace directory")
    for relative in spec.test_paths:
        check()
        path = root / relative
        for part in (path, *path.parents):
            if part == root.parent:
                break
            if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
                raise ExecutionUnavailable("CheckSpec test obligation contains a link")
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ExecutionUnavailable("CheckSpec test obligation is missing or escapes the workspace")
    identity = spec.sympy_identity if spec.runner == "sympy-native" else spec.pytest_self_identity if spec.runner == "pytest-self" else None
    if identity is not None:
        for relative, expected in identity.source_sha256:
            check()
            path = root / relative
            if (any(part.is_symlink() or getattr(part, "is_junction", lambda: False)()
                    for part in (path, *path.parents) if part != root.parent and part.is_relative_to(root))
                    or not path.is_file() or path.stat().st_size > 4 * 1024**2):
                raise ExecutionUnavailable("Protected check runner source differs from admitted bytes")
            with path.open('rb') as source:
                raw = source.read(4 * 1024**2 + 1)
            if len(raw) > 4 * 1024**2 or hashlib.sha256(raw).hexdigest() != expected:
                raise ExecutionUnavailable("Protected check runner source differs from admitted bytes")
    from atomic_flow.agents.verify import check_source_revision
    revision = check_source_revision(root, check)
    if revision != spec.source_revision:
        raise ExecutionUnavailable("CheckSpec source revision is stale")
    return revision


def checker_args(spec: CheckSpec, nonce: str) -> list[str]:
    if (type(spec) is not CheckSpec or type(nonce) is not str
            or re.fullmatch(r"[0-9a-f]{32}", nonce) is None):
        raise ExecutionUnavailable("Unsupported CheckSpec launcher")
    if spec.runtime_identity is not None:
        if spec.runner != "sympy-native" or spec.runtime_identity.python_version != (3, 6, 15):
            raise ExecutionUnavailable("Private ordinary runner is not implemented for this runtime")
        from atomic_flow.check_private_sympy import private_sympy_args
        return private_sympy_args(spec, nonce)
    if spec.runner == "sympy-native":
        if spec.sympy_identity is None:
            raise ExecutionUnavailable("Native SymPy execution needs an admitted runner identity")
        bootstrap = _SYMPY_BOOTSTRAP.replace("PARSE_SOURCE", inspect.getsource(_parse_sympy_output))
        return ["-I", "-c", bootstrap, nonce, spec.target_root,
                json.dumps(spec.sympy_identity.as_dict(), sort_keys=True), str(spec.resources.output_bytes_per_stream), *spec.test_paths]
    if spec.runner == "pytest-self":
        if spec.pytest_self_identity is None:
            raise ExecutionUnavailable("Pytest repository self-checks need an admitted runner identity")
        return pytest_self_args(spec.pytest_self_identity, nonce, spec.target_root, spec.interpreter, spec.test_paths)
    return ["-I", "-c", _BOOTSTRAP, nonce, spec.target_root, *spec.test_paths]


@dataclass(frozen=True)
class CheckReceipt:
    spec_digest: str
    source_before: str
    source_after: str
    check_passed: bool
    runner_receipt: dict | None
    result: CommandResult
    verified_success: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _runner_receipt(result: CommandResult, spec: CheckSpec, nonce: str) -> dict | None:
    if type(result.returncode) is not int or result.timed_out or result.stdout_truncated or result.stderr_truncated:
        return None
    if spec.private_pytest_identity is not None:
        if spec.fixture_identity is None:
            return None
        if spec.fixture_identity.variant == "function36-v3" and spec.requests_ca_identity is not None:
            from atomic_flow.check_requests_function import function_receipt
            return function_receipt(result, spec, nonce)
        if spec.fixture_identity.variant != "3362-cn":
            return None
        from atomic_flow.check_requests3362 import requests3362_receipt
        return requests3362_receipt(result, spec, nonce)
    if spec.runner == "pytest-self":
        if spec.pytest_self_identity is None:
            return None
        return pytest_self_receipt(result, spec.pytest_self_identity, nonce, spec.target_root, spec.interpreter, spec.test_paths)
    lines = [line[len(_MARKER):] for line in result.stdout.splitlines() if line.startswith(_MARKER)]
    if len(lines) != 1:
        return None
    try:
        receipt = json.loads(lines[0])
    except (TypeError, ValueError):
        return None
    if spec.runner == "sympy-native":
        return _sympy_runner_receipt(result, spec, nonce, receipt)
    if (not isinstance(receipt, dict) or receipt.get("nonce") != nonce or receipt.get("root") != spec.target_root
            or receipt.get("interpreter") != spec.interpreter or receipt.get("finished") is not True
            or type(receipt.get("collected")) is not int or receipt["collected"] <= 0
            or receipt.get("files") != sorted(spec.test_paths) or receipt.get("skipped") != 0
            or type(receipt.get("failed")) is not int or receipt["failed"] < 0
            or receipt.get("exit_code") != result.returncode
            or any(type(receipt.get(key)) is not int for key in ("skipped", "failed", "exit_code"))
            or not isinstance(receipt.get("pytest_module"), str)
            or not receipt["pytest_module"].startswith("/")
            or receipt["pytest_module"].startswith(spec.target_root + "/")
            or not isinstance(receipt.get("pytest_version"), str)):
        return None
    phases = ("call_failed", "setup_failed", "teardown_failed")
    if any(key in receipt for key in phases):
        if (any(type(receipt.get(key)) is not int or receipt[key] < 0 for key in phases)
                or sum(receipt[key] for key in phases) != receipt["failed"]):
            return None
    if result.returncode == 0:
        if receipt["failed"] != 0:
            return None
    elif result.returncode == 1:
        if (any(type(receipt.get(key)) is not int or receipt[key] < 0 for key in ("call_failed", "setup_failed", "teardown_failed"))
                or receipt["call_failed"] <= 0 or receipt["call_failed"] > receipt["collected"]
                or receipt["setup_failed"] != 0 or receipt["teardown_failed"] != 0
                or receipt["failed"] != receipt["call_failed"]):
            return None
    else:
        return None
    return receipt


def _sympy_runner_receipt(result: CommandResult, spec: CheckSpec, nonce: str, receipt: dict) -> dict | None:
    identity = spec.sympy_identity
    if (identity is None or not isinstance(receipt, dict) or result.returncode not in {0, 1}
            or receipt.get("nonce") != nonce or receipt.get("root") != spec.target_root
            or receipt.get("interpreter") != spec.interpreter or receipt.get("runner") != "sympy-native"
            or receipt.get("finished") is not True or type(receipt.get("exit_code")) is not int
            or receipt["exit_code"] != result.returncode
            or receipt.get("sympy_module") != spec.target_root + "/sympy/__init__.py"
            or receipt.get("sympy_version") != identity.sympy_version
            or receipt.get("python_version") != list(identity.python_version)
            or receipt.get("native_source_sha256") != dict(identity.source_sha256)
            or receipt.get("native_argv") != [spec.target_root + "/bin/test", "-C", "--verbose", *spec.test_paths]):
        return None
    runtime = spec.runtime_identity
    if runtime is not None and (
            receipt.get("runtime_digest") != runtime.digest
            or receipt.get("check_spec_digest") != spec.digest
            or receipt.get("source_revision") != spec.source_revision
            or receipt.get("launcher_sha256") != runtime.launcher_sha256
            or receipt.get("dependency_inventory_sha256") != runtime.inventory_sha256
            or receipt.get("mpmath_version") != "1.2.1"
            or not isinstance(receipt.get("mpmath_module"), str)
            or not receipt["mpmath_module"].startswith(runtime.private_prefix + "/lib/python3.6/site-packages/mpmath/")
            or type(receipt.get("mpmath_module_sha256")) is not str
            or re.fullmatch(r"[0-9a-f]{64}", receipt["mpmath_module_sha256"]) is None):
        return None
    if (type(receipt.get("collected")) is not int or not isinstance(receipt.get("counts"), dict)
            or any(type(value) is not int for value in receipt["counts"].values())):
        return None
    begin, end = "ATOMIC_SYMPY_BEGIN:" + nonce, "ATOMIC_SYMPY_END:" + nonce
    lines = result.stdout.splitlines(keepends=True)
    begins = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == begin]
    ends = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == end]
    if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
        return None
    body = "".join(lines[begins[0] + 1:ends[0]])
    try:
        summary = _parse_sympy_output(body, spec.test_paths, result.returncode)
    except (TypeError, ValueError):
        return None
    if (any(receipt.get(key) != value for key, value in summary.items())
            or receipt.get("native_stdout_sha256") != hashlib.sha256(body.encode()).hexdigest()
            or result.returncode == 0 and any(summary["counts"][name] for name in ("failed", "exceptions", "skipped", "xpassed"))):
        return None
    return receipt


def completed_check_receipt(spec: CheckSpec, receipt: CheckReceipt, *, negative: bool = False) -> bool:
    """Revalidate a host receipt; arbitrary output flags never attest completion.

    The target interpreter's report is not an independent oracle or signature.
    This checks the actual stored stream and the immutable host/source binding.
    """
    if (type(spec) is not CheckSpec or type(receipt) is not CheckReceipt
            or receipt.spec_digest != spec.digest or receipt.source_before != spec.source_revision
            or receipt.source_after != spec.source_revision or not isinstance(receipt.runner_receipt, dict)):
        return False
    nonce = receipt.runner_receipt.get("nonce")
    if type(nonce) is not str or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        return False
    validated = _runner_receipt(receipt.result, spec, nonce)
    if validated is None or validated != receipt.runner_receipt:
        return False
    if not negative:
        return True
    if receipt.check_passed is not False or receipt.result.returncode != 1:
        return False
    if spec.runner in {"pytest", "pytest-self"}:
        return validated.get("call_failed", 0) > 0
    counts = validated.get("counts", {})
    return (type(counts.get("failed")) is int and counts["failed"] > 0
            and all(counts.get(name) == 0 for name in ("exceptions", "skipped", "xpassed")))


def run_check_spec(spec: CheckSpec, workspace: Path, *, cancel_check=None) -> CheckReceipt:
    """Run the complete host obligation; uncertain execution raises fail-closed.

    Callers must supply an already admitted private workspace, image and spec.
    This function does not acquire approval or publish target writes.
    """
    from atomic_flow.agents.execution import run_python
    from atomic_flow.agents.verify import check_source_revision
    if type(spec) is not CheckSpec:
        raise ExecutionUnavailable("An immutable host-owned CheckSpec is required")
    if spec.private_pytest_identity is not None:
        if (spec.fixture_identity is not None and spec.fixture_identity.variant == "function36-v3"
                and spec.requests_ca_identity is not None):
            from atomic_flow.check_requests_function_execution import run_function36_check
            receipt, _, _ = run_function36_check(spec, workspace, cancel_check=cancel_check)
            return receipt
        if spec.fixture_identity is None or spec.fixture_identity.variant != "3362-cn":
            raise ExecutionUnavailable("Private Requests fixture lifecycle is not implemented for this cohort")
        from atomic_flow.check_requests_execution import run_requests3362_check
        receipt, _ = run_requests3362_check(spec, workspace, cancel_check=cancel_check)
        return receipt
    config = ExecutionConfig(mode="docker", profile="checks", image=spec.image,
                             output_bytes=spec.resources.output_bytes_per_stream)
    args = list(spec.argv[1:])
    before = admit_check_spec(spec, workspace, config, spec.timeout_seconds, args, cancel_check=cancel_check)
    nonce = uuid.uuid4().hex
    result = run_python(args, workspace, config=config, timeout=spec.timeout_seconds,
                        cancel_check=cancel_check, host_spec=spec, host_spec_nonce=nonce)
    after = check_source_revision(workspace, cancel_check)
    receipt = _runner_receipt(result, spec, nonce)
    passed = before == after and receipt is not None and result.returncode == 0
    return CheckReceipt(spec.digest, before, after, passed, receipt, result)


def run_private_syntax(spec: CheckSpec, workspace: Path, path: str, *, cancel_check=None) -> dict:
    """Compile one confined file with its admitted target grammar, never execute it.

    Whole-source freshness, exact runtime/spec binding and container cleanup are
    mandatory. This observation never satisfies the ordinary-test obligation.
    """
    from atomic_flow.agents.execution import run_python
    from atomic_flow.agents.source import confined_path
    from atomic_flow.agents.verify import check_source_revision
    from atomic_flow.check_private_runtime import MAX_SOURCE_BYTES, PrivateSyntaxRequest, private_syntax_receipt

    if type(spec) is not CheckSpec or spec.runtime_identity is None:
        raise ExecutionUnavailable("Private syntax requires a host runtime-bound specification")
    config = ExecutionConfig(mode="docker", profile="checks", image=spec.image,
                             output_bytes=spec.resources.output_bytes_per_stream)
    args = list(spec.argv[1:])
    before = admit_check_spec(spec, workspace, config, spec.timeout_seconds, args, cancel_check=cancel_check)
    source = confined_path(workspace, path)
    if not source.is_file() or source.stat().st_size > MAX_SOURCE_BYTES:
        raise ExecutionUnavailable("Private syntax source is missing or oversized")
    with source.open("rb") as stream:
        data = stream.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ExecutionUnavailable("Private syntax source exceeds its admitted byte limit")
    request = PrivateSyntaxRequest(spec.runtime_identity, spec.digest, before, path,
                                   hashlib.sha256(data).hexdigest(), uuid.uuid4().hex, spec.target_root)
    result = run_python(args, workspace, config=config, timeout=spec.timeout_seconds,
                        cancel_check=cancel_check, host_spec=spec, host_spec_nonce=request.nonce,
                        host_syntax_request=request)
    after = check_source_revision(workspace, cancel_check)
    receipt = private_syntax_receipt(result, request)
    if before != after or receipt is None:
        raise ExecutionUnavailable("Private syntax execution was incomplete or source identity changed")
    return receipt
