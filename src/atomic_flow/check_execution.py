"""Execute an admitted pytest CheckSpec with the existing Docker lifecycle.

TaskRunner's host CheckObligation uses this boundary for tool, edit, step and
final checks. A target process can lie about its own pytest hooks/output; its
receipt is not an independent oracle grade or remote attestation. Native SymPy
deliberately refuses until a counted native runner contract is implemented.
Historical pytest repository self-tests are also unsupported: this launcher
deliberately imports an installed checker before permitting target imports.
"""
from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from atomic_flow.agents.execution import CommandResult, ExecutionUnavailable
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
    def pytest_sessionfinish(self,session,exitstatus):
        self.finished = True
evidence = Evidence()
code = int(pytest.main(['-q','--no-header','-x',*paths],plugins=[evidence]))
receipt = {'nonce':nonce,'root':root,'interpreter':sys.executable,
           'interpreter_resolved':os.path.realpath(sys.executable),
           'pytest_module':runner_path,'pytest_version':getattr(pytest,'__version__','unknown'),
           'collected':evidence.collected,'files':sorted(evidence.files),
           'skipped':evidence.skipped,'failed':evidence.failed,'finished':evidence.finished,
           'exit_code':code}
print('\nATOMIC_CHECK_SPEC_RECEIPT:' + json.dumps(receipt,sort_keys=True),flush=True)
complete = (evidence.finished and evidence.collected > 0 and evidence.files == set(paths)
            and evidence.skipped == 0 and evidence.failed == 0)
raise SystemExit(0 if code == 0 and complete else code if code else 5)
'''


def admit_check_spec(spec: CheckSpec, workspace: Path, config: ExecutionConfig,
                     timeout: float, args: list[str], *, cancel_check=None) -> str:
    """Validate host inputs before any subprocess; validation is not provenance."""
    if type(spec) is not CheckSpec:
        raise ExecutionUnavailable("An immutable host-owned CheckSpec is required")
    check = cancel_check or (lambda: None)
    check()
    if spec.runner != "pytest":
        raise ExecutionUnavailable("Native SymPy CheckSpec execution is not implemented")
    if spec.task_id.startswith("pytest-dev__pytest-"):
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
    from atomic_flow.agents.verify import check_source_revision
    revision = check_source_revision(root, check)
    if revision != spec.source_revision:
        raise ExecutionUnavailable("CheckSpec source revision is stale")
    return revision


def checker_args(spec: CheckSpec, nonce: str) -> list[str]:
    if (type(spec) is not CheckSpec or spec.runner != "pytest" or type(nonce) is not str
            or re.fullmatch(r"[0-9a-f]{32}", nonce) is None):
        raise ExecutionUnavailable("Unsupported CheckSpec launcher")
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
    if result.timed_out or result.stdout_truncated or result.stderr_truncated:
        return None
    lines = [line[len(_MARKER):] for line in result.stdout.splitlines() if line.startswith(_MARKER)]
    if len(lines) != 1:
        return None
    try:
        receipt = json.loads(lines[0])
    except (TypeError, ValueError):
        return None
    if (not isinstance(receipt, dict) or receipt.get("nonce") != nonce or receipt.get("root") != spec.target_root
            or receipt.get("interpreter") != spec.interpreter or receipt.get("finished") is not True
            or type(receipt.get("collected")) is not int or receipt["collected"] <= 0
            or receipt.get("files") != sorted(spec.test_paths) or receipt.get("skipped") != 0
            or receipt.get("failed") != 0 or receipt.get("exit_code") != result.returncode
            or any(type(receipt.get(key)) is not int for key in ("skipped", "failed", "exit_code"))
            or not isinstance(receipt.get("pytest_module"), str)
            or not receipt["pytest_module"].startswith("/")
            or receipt["pytest_module"].startswith(spec.target_root + "/")
            or not isinstance(receipt.get("pytest_version"), str)):
        return None
    return receipt


def run_check_spec(spec: CheckSpec, workspace: Path, *, cancel_check=None) -> CheckReceipt:
    """Run the complete host obligation; uncertain execution raises fail-closed.

    Callers must supply an already admitted private workspace, image and spec.
    This function does not acquire approval or publish target writes.
    """
    from atomic_flow.agents.execution import run_python
    from atomic_flow.agents.verify import check_source_revision
    if type(spec) is not CheckSpec:
        raise ExecutionUnavailable("An immutable host-owned CheckSpec is required")
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
