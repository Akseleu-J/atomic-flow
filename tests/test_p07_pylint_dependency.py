"""Pylint overlays require historical pins and satisfied target runtime constraints."""
import copy
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("p07_pylint_builder", ROOT / "tools/p07_dependency_image.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
spec = importlib.util.spec_from_file_location("evaluation_stage4", ROOT / "tools/evaluation_stage4.py")
stage4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage4)
sys.modules.setdefault("evaluation_stage4", stage4)
spec = importlib.util.spec_from_file_location("p07_pylint_evaluator", ROOT / "tools/evaluation_p07_isolated.py")
evaluator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)
PROFILE = ROOT / "benchmarks/stage4/p07-pylint-environment-profile.json"


def constraints(wheels):
    environment = {**default_environment(), "extra": "", "python_version": "3.9", "python_full_version": "3.9.20",
                   "implementation_name": "cpython", "sys_platform": "linux", "os_name": "posix", "platform_system": "Linux"}
    versions = {"lazy-object-proxy": "1.10.0", "wrapt": "1.16.0", "typing-extensions": "4.12.2", "setuptools": "75.1.0"}
    proof = {"environment": environment, "packages": {}}
    for wheel in wheels:
        requirements = builder.declared_requirements(wheel)
        if not requirements:
            continue
        checks = []
        for text in sorted(requirements):
            requirement = Requirement(text)
            active = requirement.marker is None or requirement.marker.evaluate(environment)
            checks.append({"requirement": text, "active": active, "version": versions[requirement.name] if active else None})
        proof["packages"][wheel["name"].lower()] = {"installed_requires_dist": requirements,
            "planned_requires_dist": requirements, "checks": checks}
    return proof


@pytest.mark.parametrize("task,expected", [
    ("pylint-dev__pylint-7080", {"astroid": "2.11.6", "tomlkit": "0.10.1"}),
    ("pylint-dev__pylint-7114", {"astroid": "2.12.2"}),
])
def test_each_task_selects_its_exact_historical_wheel_subset(task, expected):
    config = json.loads(PROFILE.read_text())
    _, pins, wheels = builder.selected_wheels(config, task)
    assert pins == expected
    assert {item["name"].lower(): item["version"] for item in wheels} == expected
    assert len(wheels) == len(expected)
    proof = constraints(wheels)
    builder.validate_requirements_proof(proof, wheels, strict=True)
    typed = next(item for item in proof["packages"]["astroid"]["checks"] if item["requirement"].startswith("typed-ast"))
    assert not typed["active"] and typed["version"] is None


def test_cross_task_astroid_or_unreviewed_tomlkit_cannot_change_profile_pins():
    config = json.loads(PROFILE.read_text())
    config["tasks"][0]["pins"]["astroid"] = "2.12.2"
    with pytest.raises(ValueError, match="pins"):
        builder.selected_wheels(config, "pylint-dev__pylint-7080")
    with pytest.raises(ValueError):
        builder.task_pins("pylint-dev__pylint-7993")


def test_astroid_wheel_metadata_must_exactly_reproduce_reviewed_requires_dist():
    config = json.loads(PROFILE.read_text())
    record = next(item for item in config["wheels"] if item["name"] == "astroid" and item["version"] == "2.12.2")

    def wheel(requires):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("astroid-2.12.2.dist-info/METADATA", "Name: astroid\nVersion: 2.12.2\n" +
                             "".join("Requires-Dist: " + text + "\n" for text in requires))
            archive.writestr("astroid/__init__.py", "raise AssertionError('never import on host')")
        return output.getvalue()

    raw = wheel(record["requires_dist"])
    assert builder.validate_wheel({**record, "sha256": builder.sha(raw)}, raw) == "astroid"
    unreviewed = wheel([*record["requires_dist"], "unexpected-package>=1"])
    with pytest.raises(ValueError, match="metadata"):
        builder.validate_wheel({**record, "sha256": builder.sha(unreviewed)}, unreviewed)
    with pytest.raises(ValueError, match="runtime dependencies"):
        builder.declared_requirements({**record, "requires_dist": []})


@pytest.mark.parametrize("change", ["unsatisfied", "missing", "marker", "environment", "metadata", "missing_check"])
def test_runtime_constraints_are_not_satisfied_by_freeze_or_metadata_alone(change):
    config = json.loads(PROFILE.read_text())
    _, _, wheels = builder.selected_wheels(config, "pylint-dev__pylint-7080")
    proof = constraints(wheels)
    astroid = proof["packages"]["astroid"]
    wrapt = next(item for item in astroid["checks"] if item["requirement"].startswith("wrapt"))
    if change == "unsatisfied":
        wrapt["version"] = "2.0.0"
    elif change == "missing":
        wrapt["version"] = None
    elif change == "marker":
        wrapt["active"] = False
    elif change == "environment":
        proof["environment"]["python_version"] = "3.11"
    elif change == "metadata":
        astroid["installed_requires_dist"] = []
    elif change == "missing_check":
        astroid["checks"].pop()
    with pytest.raises(ValueError):
        builder.validate_requirements_proof(proof, wheels, strict=True)


def test_probe_program_checks_before_and_strictly_after_without_installing_dependencies():
    config = json.loads(PROFILE.read_text())
    _, _, wheels = builder.selected_wheels(config, "pylint-dev__pylint-7080")
    before = builder.requirements_probe_source(wheels, strict=False)
    after = builder.requirements_probe_source(wheels, strict=True)
    assert "if False and sorted(actual)" in before and "if True and sorted(actual)" in after
    assert "m.version(requirement.name) if active else None" in before
    assert "requirement.specifier.contains" in after
    assert "pip install" not in before + after and "subprocess" not in before + after


def pylint_receipt(tmp_path):
    config = json.loads(PROFILE.read_text())
    selected, pins, records = builder.selected_wheels(config, "pylint-dev__pylint-7114")
    wheels = [builder.serialized_wheel(item) for item in records]
    task = {"instance_id": selected["instance_id"], "repo": "pylint-dev/pylint", "base_commit": selected["base_commit"]}
    raw = PROFILE.read_bytes().replace(b"\r\n", b"\n")
    digest, image_id = selected["base_image_digest"], "sha256:" + "b" * 64
    base = {"Id": "sha256:" + "a" * 64, "RepoDigests": [digest], "RootFS": {"Type": "layers", "Layers": ["sha256:" + "c" * 64]}}
    image = {"Id": image_id, "RootFS": {"Type": "layers", "Layers": [*base["RootFS"]["Layers"], "sha256:" + "d" * 64]},
             "Config": {"Labels": {"atomic.p07.dependencies.base": digest, "atomic.p07.dependencies.profile": builder.sha(raw)}}}
    source = {"head": "e" * 40, "diff": "", "status": ""}
    report = {"status": "dependency_overlay_built_source_unchanged", "task": task["instance_id"], "base_image": digest,
        "base_commit": task["base_commit"], "profile_sha256": builder.sha(raw), "image_id": image_id,
        "base_image_inspect": base, "image_inspect": image, "wheels": wheels,
        "requirements_sha256": builder.sha(builder.requirements_lock(wheels).encode()), "model_calls": 0,
        "provider_credentials": "none", "target_setup_or_tests_executed": False,
        "cleanup_exit": 0, "cleanup": [{"container": name, "exit": 0, "confirmed": True} for name in ("before", "after")],
        "before": {**source, "pip_freeze": "astroid==2.12.13\npytest==7.4.4", "overlay_versions": {"astroid": "2.12.13"},
                   "runtime_requirements": constraints(wheels)},
        "after": {**source, "pip_freeze": "astroid==2.12.2\npytest==7.4.4", "overlay_versions": pins,
                  "runtime_requirements": constraints(wheels)}}
    return task, image_id, digest, base, image, report


def test_pylint_admission_and_fresh_container_repeat_runtime_constraint_checks(tmp_path, monkeypatch):
    task, image_id, digest, base, image, report = pylint_receipt(tmp_path)
    path = tmp_path / "dependency-image.json"
    path.write_text(json.dumps(report))
    admitted = evaluator.validate_dependency_receipt(task, image_id, digest, base, image, tmp_path, PROFILE)
    assert admitted["pins"] == {"astroid": "2.12.2"}
    calls = []

    def command(args):
        calls.append(args)
        if "rev-parse" in args:
            return admitted["source"]["head"]
        if "diff" in args or "status" in args:
            return ""
        assert args[3:5] == [builder.PYTHON, "-I"]
        if "freeze" in args:
            return admitted["pip_freeze"]
        if "from packaging.markers" in args[-1]:
            return json.dumps(admitted["runtime_requirements"])
        return json.dumps(admitted["pins"])

    monkeypatch.setattr(evaluator, "command", command)
    evaluator.check_dependency_container("fresh", admitted)
    assert any("from packaging.markers" in args[-1] for args in calls)
    altered = copy.deepcopy(admitted["runtime_requirements"])
    next(item for item in altered["packages"]["astroid"]["checks"] if item["requirement"].startswith("wrapt"))["version"] = "2.0.0"
    admitted["runtime_requirements"] = altered
    with pytest.raises(ValueError, match="unsatisfied"):
        evaluator.check_dependency_container("fresh", admitted)


def test_pylint_build_receipt_without_before_dependency_proof_is_not_admitted(tmp_path):
    task, image_id, digest, base, image, report = pylint_receipt(tmp_path)
    report["before"].pop("runtime_requirements")
    (tmp_path / "dependency-image.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="requirement proof"):
        evaluator.validate_dependency_receipt(task, image_id, digest, base, image, tmp_path, PROFILE)
