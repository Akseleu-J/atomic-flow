"""Post-edit verification for Python source files."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from atomic_flow.agents.diagnostics import syntax_diagnostic
from atomic_flow.agents.execution import checked_workspace, run_python
from atomic_flow.agents.source import confined_path, digest
from atomic_flow.config import ExecutionConfig
from atomic_flow.tree.defaults import MAX_FILE_BYTES


@dataclass
class VerificationResult:
    ok: bool
    checks: list[str] = field(default_factory=list)
    message: str = ""
    test_output: str = ""
    diagnostics: list[dict] = field(default_factory=list)
    source_hash: str = ""
    timed_out: bool = False
    exit_code: int | None = None
    checked_source_revision: str = ""
    source_changed: bool = False
    check_observation: dict = field(default_factory=dict)


def check_source_snapshot(root: Path, cancel_check=None) -> dict:
    """Execution-boundary audit over the same admitted snapshot as changesets."""
    from atomic_flow.agents.changeset import _files
    from atomic_flow.config import user_dir
    return _files(root, user_dir().resolve(), cancel_check=cancel_check or (lambda: None))


def check_source_revision(root: Path, cancel_check=None) -> str:
    from atomic_flow.agents.step_evidence import manifest_digest
    return manifest_digest(check_source_snapshot(root, cancel_check))


def check_source_changed(before: dict, after: dict) -> bool:
    from atomic_flow.tree.defaults import SOURCE_SUFFIXES
    # Preserve every original input, including data fixtures. New code/config
    # is a conflict. Generated data outputs in disposable copies are allowed;
    # persistent TaskRunner workspaces still enforce the full publication scope.
    return (any(after.get(p) != h for p, h in before.items())
            or any(Path(p).suffix.lower() in SOURCE_SUFFIXES for p in after.keys() - before.keys()))


def _matching_tests(project_root: Path, rel_path: str) -> list[str]:
    """Find tests likely to cover a changed source file."""
    stem = Path(rel_path).stem
    if stem.startswith("test_"):
        return []
    candidates: list[str] = []
    for pattern in (f"tests/test_{stem}.py", f"tests/**/test_{stem}.py",
                    f"tests/test_{stem}_*.py", f"tests/**/test_{stem}_*.py"):
        candidates.extend(path.relative_to(project_root).as_posix() for path in project_root.glob(pattern))
    return sorted(set(candidates))


def verify_edit(project_root: Path, rel_path: str, *, run_tests: bool = True,
                timeout_seconds: int = 120, execution: ExecutionConfig | None = None,
                cancel_check: Callable[[], None] | None = None, private_workspace: bool = False, check_obligation=None) -> VerificationResult:
    if cancel_check:
        cancel_check()
    try:
        full = confined_path(project_root, rel_path)
        if not full.is_file() or full.stat().st_size > MAX_FILE_BYTES:
            raise OSError("missing or oversized source")
        data = full.read_bytes()
        source_hash = digest(data)
    except OSError:
        return VerificationResult(ok=False, checks=["exists"], message=f"file missing, unsafe or oversized: {rel_path}")
    if check_obligation is not None:
        from atomic_flow.check_obligation import check_observation, run_obligation_check
        if rel_path.endswith((".py", ".pyi")):
            try:
                compile(data, rel_path, "exec", dont_inherit=True)
            except SyntaxError as exc:
                return VerificationResult(ok=False, checks=["syntax"], message=str(exc)[:500],
                                          diagnostics=[syntax_diagnostic(rel_path, data, exc)], source_hash=source_hash)
        with checked_workspace(project_root, private_workspace=private_workspace, cancel_check=cancel_check) as check_root:
            spec, receipt = run_obligation_check(check_obligation, check_root, cancel_check=cancel_check)
        result = receipt.result
        return VerificationResult(ok=receipt.check_passed, checks=["syntax", "ordinary_test"],
                                  message="complete host ordinary-test obligation " + ("passed" if receipt.check_passed else "failed or incomplete"),
                                  test_output=(result.stdout + result.stderr)[-3000:], source_hash=source_hash,
                                  timed_out=result.timed_out, exit_code=result.returncode,
                                  checked_source_revision=spec.source_revision,
                                  source_changed=receipt.source_before != receipt.source_after,
                                  check_observation=check_observation(check_obligation, spec, receipt))
    if not rel_path.endswith((".py", ".pyi")):
        return VerificationResult(ok=True, checks=["skip"], message="non-Python file; only structural check", source_hash=source_hash)

    compile_revision = check_source_revision(project_root, cancel_check)
    if digest(full.read_bytes()) != source_hash:
        return VerificationResult(False, ["syntax"], "source changed before compile", source_changed=True)
    try:
        compile(data, rel_path, "exec", dont_inherit=True)  # Compile bytes without executing or creating __pycache__.
    except SyntaxError as exc:
        return VerificationResult(ok=False, checks=["syntax"], message=str(exc)[:500],
                                  diagnostics=[syntax_diagnostic(rel_path, data, exc)], source_hash=source_hash,
                                  checked_source_revision=compile_revision)
    if not run_tests:
        return VerificationResult(ok=True, checks=["syntax"], message="compile OK (tests skipped)", source_hash=source_hash)

    test_files = _matching_tests(project_root, rel_path)
    if not test_files:
        return VerificationResult(ok=True, checks=["syntax"], message="compile OK; no matching tests", source_hash=source_hash)
    with checked_workspace(project_root, private_workspace=private_workspace, cancel_check=cancel_check) as check_root:
        from atomic_flow.agents.step_evidence import manifest_digest
        checked_snapshot = check_source_snapshot(check_root, cancel_check)
        checked_revision = manifest_digest(checked_snapshot)
        result = run_python(["-m", "pytest", "-q", "--no-header", "-x", *test_files],
                            check_root, config=execution, timeout=timeout_seconds, cancel_check=cancel_check)
        changed = check_source_changed(checked_snapshot, check_source_snapshot(check_root, cancel_check))
    if changed:
        return VerificationResult(False, ["syntax", "pytest"], "source changed during check",
                                  source_hash=source_hash, source_changed=True, checked_source_revision=checked_revision)
    if result.timed_out:
        return VerificationResult(ok=False, checks=["syntax", "pytest"],
                                  message=f"pytest timed out after {timeout_seconds}s", source_hash=source_hash,
                                  timed_out=True, exit_code=result.returncode, checked_source_revision=checked_revision)
    output = (result.stdout + result.stderr)[-3000:]
    ok = result.returncode == 0
    return VerificationResult(ok=ok, checks=["syntax", "pytest"], exit_code=result.returncode,
                              checked_source_revision=checked_revision,
                              message=f"pytest {'PASS' if ok else 'FAIL'} on {test_files}",
                              test_output=output, source_hash=source_hash)
