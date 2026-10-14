"""Post-edit verification for Python source files."""
from __future__ import annotations

import py_compile
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VerificationResult:
    ok: bool
    checks: list[str] = field(default_factory=list)
    message: str = ""
    test_output: str = ""


def _matching_tests(project_root: Path, rel_path: str) -> list[str]:
    """Find tests likely to cover a changed source file."""
    stem = Path(rel_path).stem
    if stem.startswith("test_"):
        return []
    candidates: list[str] = []
    for pattern in (f"tests/test_{stem}.py", f"tests/**/test_{stem}.py",
                    f"tests/test_{stem}_*.py", f"tests/**/test_{stem}_*.py"):
        candidates.extend(str(path.relative_to(project_root)) for path in project_root.glob(pattern))
    return sorted(set(candidates))


def verify_edit(project_root: Path, rel_path: str, *, run_tests: bool = True,
                timeout_seconds: int = 120) -> VerificationResult:
    full = project_root / rel_path
    if not full.exists():
        return VerificationResult(ok=False, checks=["exists"], message=f"file missing: {rel_path}")
    if not rel_path.endswith((".py", ".pyi")):
        return VerificationResult(ok=True, checks=["skip"], message="non-Python file; only structural check")

    try:
        py_compile.compile(str(full), doraise=True)
    except py_compile.PyCompileError as exc:
        return VerificationResult(ok=False, checks=["syntax"], message=str(exc)[:500])
    if not run_tests:
        return VerificationResult(ok=True, checks=["syntax"], message="py_compile OK (tests skipped)")

    test_files = _matching_tests(project_root, rel_path)
    if not test_files:
        return VerificationResult(ok=True, checks=["syntax"], message="py_compile OK; no matching tests")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", "-x", *test_files],
            cwd=project_root, capture_output=True, text=True, timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return VerificationResult(ok=False, checks=["syntax", "pytest"],
                                  message=f"pytest timed out after {timeout_seconds}s")
    output = (result.stdout + result.stderr)[-3000:]
    ok = result.returncode == 0
    return VerificationResult(ok=ok, checks=["syntax", "pytest"],
                              message=f"pytest {'PASS' if ok else 'FAIL'} on {test_files}",
                              test_output=output)
