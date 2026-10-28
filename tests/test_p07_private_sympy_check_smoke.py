"""Pure archive/probe validation tests; no target code or Docker execution."""
import ast
import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"


@pytest.fixture
def smoke(monkeypatch):
    monkeypatch.syspath_prepend(str(TOOLS))
    module_spec = importlib.util.spec_from_file_location("p07_private_smoke_test", TOOLS / "p07_private_sympy_check_smoke.py")
    module = importlib.util.module_from_spec(module_spec)
    # Avoid loading the historical oracle's unrelated optional dependencies.
    from types import ModuleType
    calibration = ModuleType("p07_sympy13971_calibrate")
    calibration.RUNNER_SOURCES = {}
    calibration.runtime_admission = lambda *args: None
    monkeypatch.setitem(sys.modules, "p07_sympy13971_calibrate", calibration)
    module_spec.loader.exec_module(module)
    return module


def archive(tmp_path, entries):
    path = tmp_path / "mpmath.whl"
    with zipfile.ZipFile(path, "w") as wheel:
        for name, raw in entries:
            wheel.writestr(name, raw)
    return {"wheel": {"filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}


def test_complete_wheel_package_inventory_does_not_import_target(smoke, tmp_path):
    entries = [("mpmath/__init__.py", b"raise RuntimeError('must never import')\n"),
               ("mpmath/libmp/libintmath.py", b"value = 1\n"),
               ("mpmath-1.2.1.dist-info/RECORD", b"metadata")]
    report = archive(tmp_path, entries)
    assert smoke.wheel_inventory(tmp_path, report) == {
        name: hashlib.sha256(raw).hexdigest() for name, raw in entries if name.startswith("mpmath/")}


@pytest.mark.parametrize("name", ["mpmath/../escape.py", "mpmath//bad.py", "mpmath/bad;name.py"])
def test_wheel_inventory_refuses_unsafe_paths(smoke, tmp_path, name):
    report = archive(tmp_path, [(name, b"value = 1")])
    with pytest.raises(ValueError):
        smoke.wheel_inventory(tmp_path, report)


def test_wheel_inventory_refuses_changed_archive(smoke, tmp_path):
    report = archive(tmp_path, [("mpmath/__init__.py", b"value = 1")])
    report["wheel"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="identity"):
        smoke.wheel_inventory(tmp_path, report)


def test_fixed_inventory_probe_is_python36_compatible_and_hashes_all_expected_files(smoke):
    expected = {"mpmath/__init__.py": "a" * 64, "mpmath/libmp/helper.py": "b" * 64}
    source = smoke.inventory_probe_source(expected)
    tree = ast.parse(source, feature_version=(3, 6))
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "expected" for target in node.targets))
    assert json.loads(ast.literal_eval(assignment.value.args[0])) == expected
    imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert imports == {"hashlib", "json", "os", "sys"}
    assert "if actual_files!=set(expected):raise SystemExit(125)" in source


def test_distinct_syntax_cases_are_actual_python36_grammar_examples(smoke):
    ast.parse(smoke.VALID_SOURCE, feature_version=(3, 6))
    with pytest.raises(SyntaxError):
        ast.parse(smoke.WALRUS_SOURCE, feature_version=(3, 6))


@pytest.mark.parametrize("label", ["base_fail", "edited_pass", "required_skip"])
def test_native_case_validator_preserves_pass_fail_skip_and_all_identity_bindings(smoke, monkeypatch, label):
    from types import SimpleNamespace

    monkeypatch.setattr(smoke, "completed_check_receipt", lambda *args, **kwargs: label != "required_skip")
    runtime = SimpleNamespace(digest="a" * 64, launcher_sha256="b" * 64)
    identity = SimpleNamespace(source_sha256=(("bin/test", "c" * 64),))
    spec = SimpleNamespace(digest="d" * 64, runtime_identity=runtime, sympy_identity=identity)
    result = SimpleNamespace(returncode={"base_fail": 1, "edited_pass": 0, "required_skip": 5}[label],
                             timed_out=False, stdout_truncated=False, stderr_truncated=False)
    receipt = SimpleNamespace(source_before="e" * 64, source_after="e" * 64, spec_digest=spec.digest,
                              check_passed=label == "edited_pass", verified_success=False, result=result)
    counts = {"passed": 2 if label == "edited_pass" else 1, "failed": int(label == "base_fail"),
              "skipped": int(label == "required_skip"), "xfailed": 0, "xpassed": 0, "exceptions": 0}
    observed = {"counts": counts, "collected": 2, "files": [smoke.TEST_PATH], "finished": True,
                "runtime_digest": runtime.digest, "check_spec_digest": spec.digest, "source_revision": "e" * 64,
                "launcher_sha256": runtime.launcher_sha256, "mpmath_module_sha256": "f" * 64,
                "mpmath_version": "1.2.1", "python_version": [3, 6, 15],
                "native_source_sha256": dict(identity.source_sha256)}
    assert smoke.validate_native_case(label, spec, receipt, observed, "e" * 64, "f" * 64)
    for field in ("runtime_digest", "check_spec_digest", "source_revision", "launcher_sha256", "mpmath_module_sha256"):
        assert not smoke.validate_native_case(label, spec, receipt, observed | {field: "0" * 64}, "e" * 64, "f" * 64)
    assert not smoke.validate_native_case(label, spec, receipt, observed | {"counts": counts | {"exceptions": 1}}, "e" * 64, "f" * 64)
