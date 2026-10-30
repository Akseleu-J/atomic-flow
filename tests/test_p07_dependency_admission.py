"""Dependency remediation cannot silently change target source or other packages."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evaluation_stage4", ROOT / "tools/evaluation_stage4.py")
stage4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage4)
sys.modules.setdefault("evaluation_stage4", stage4)
spec = importlib.util.spec_from_file_location("p07_dependency_admission", ROOT / "tools/evaluation_p07_isolated.py")
p07 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p07)
PROFILE = ROOT / "benchmarks/stage4/p07-sphinx-environment-profile.json"


def receipt(tmp_path):
    raw = PROFILE.read_bytes().replace(b"\r\n", b"\n")
    profile = json.loads(raw)
    selected = profile["tasks"][0]
    task = {"instance_id": selected["instance_id"], "repo": "sphinx-doc/sphinx", "base_commit": selected["base_commit"]}
    digest, image_id = selected["base_image_digest"], "sha256:" + "b" * 64
    base = {"Id": "sha256:" + "a" * 64, "RepoDigests": [digest], "RootFS": {"Type": "layers", "Layers": ["sha256:" + "c" * 64]}}
    actual = {"Id": image_id, "RootFS": {"Type": "layers", "Layers": [*base["RootFS"]["Layers"], "sha256:" + "d" * 64]},
              "Config": {"Labels": {"atomic.p07.dependencies.base": digest, "atomic.p07.dependencies.profile": p07.sha(raw)}}}
    wheels = [{key: item[key] for key in ("name", "version", "filename", "url", "sha256")} for item in profile["wheels"]]
    requirements = "".join(f"{item['name'].lower()}=={item['version']} --hash=sha256:{item['sha256']}\n"
                           for item in sorted(wheels, key=lambda item: item["name"].lower()))
    source = {"head": "e" * 40, "diff": "", "status": ""}
    report = {"status": "dependency_overlay_built_source_unchanged", "task": task["instance_id"], "base_image": digest,
              "base_commit": task["base_commit"], "profile_sha256": p07.sha(raw), "image_id": image_id,
              "base_image_inspect": base, "image_inspect": actual, "wheels": wheels,
              "requirements_sha256": p07.sha(requirements.encode()), "model_calls": 0,
              "provider_credentials": "none", "target_setup_or_tests_executed": False,
              "cleanup_exit": 0, "cleanup": [{"container": name, "exit": 0, "confirmed": True} for name in ("before", "after")],
              "before": {**source, "pip_freeze": "docutils==0.21.2\nPygments==2.18.0\npytest==8.3.3",
                         "overlay_versions": {"docutils": "0.21.2", "pygments": "2.18.0"}},
              "after": {**source, "pip_freeze": "docutils==0.16\nPygments==2.7.4\npytest==8.3.3",
                        "overlay_versions": {"docutils": "0.16", "pygments": "2.7.4"}}}
    return task, image_id, digest, base, actual, report


def admit(tmp_path, fields, profile=PROFILE):
    task, image, digest, base, actual, report = fields
    (tmp_path / "dependency-image.json").write_text(json.dumps(report))
    return p07.validate_dependency_receipt(task, image, digest, base, actual, tmp_path, profile)


def test_exact_two_package_overlay_receipt_is_source_bound(tmp_path):
    proof = admit(tmp_path, receipt(tmp_path))
    assert proof["pins"] == {"docutils": "0.16", "pygments": "2.7.4"}
    assert proof["source"] == {"head": "e" * 40, "diff": "", "status": ""}
    assert proof["build_receipt_sha256"] == p07.sha((tmp_path / "dependency-image.json").read_bytes())
    assert "verified_success" not in proof


@pytest.mark.parametrize("change", [
    "wrong_task", "wrong_base", "mutable_image", "changed_label", "changed_ancestry", "same_layers",
    "different_base_id", "cleanup_uncertain", "cleanup_missing", "source_head", "source_diff", "source_status",
    "undeclared_package", "wrong_version", "changed_wheel", "changed_lock", "model_call", "target_setup",
])
def test_unreviewed_source_dependency_identity_and_cleanup_are_refused(tmp_path, change):
    fields = receipt(tmp_path)
    task, _, _, base, actual, report = fields
    if change == "wrong_task":
        task["repo"] = "psf/requests"
    elif change == "wrong_base":
        task["base_commit"] = "0" * 40
    elif change == "mutable_image":
        fields = (task, "mutable:latest", *fields[2:])
    elif change == "changed_label":
        actual["Config"]["Labels"]["atomic.p07.dependencies.profile"] = "0" * 64
    elif change == "changed_ancestry":
        actual["RootFS"]["Layers"][0] = "sha256:" + "0" * 64
    elif change == "same_layers":
        actual["RootFS"]["Layers"] = base["RootFS"]["Layers"][:]
    elif change == "different_base_id":
        report["base_image_inspect"] = {**base, "Id": "sha256:" + "0" * 64}
    elif change == "cleanup_uncertain":
        report["cleanup"][0]["confirmed"] = False
    elif change == "cleanup_missing":
        report["cleanup"] = []
    elif change.startswith("source_"):
        report["after"][change.removeprefix("source_")] = "0" * 40
    elif change == "undeclared_package":
        report["after"]["pip_freeze"] += "\npytest-mock==3.0.0"
    elif change == "wrong_version":
        report["after"]["overlay_versions"]["docutils"] = "0.17"
    elif change == "changed_wheel":
        report["wheels"][0]["sha256"] = "0" * 64
    elif change == "changed_lock":
        report["requirements_sha256"] = "0" * 64
    elif change == "model_call":
        report["model_calls"] = 1
    elif change == "target_setup":
        report["target_setup_or_tests_executed"] = True
    with pytest.raises(ValueError):
        admit(tmp_path, fields)


def test_profile_argument_must_equal_reviewed_checkout_bytes(tmp_path):
    changed = tmp_path / "profile.json"
    changed.write_bytes(PROFILE.read_bytes() + b" ")
    with pytest.raises(ValueError, match="reviewed checkout"):
        admit(tmp_path, receipt(tmp_path), changed)


@pytest.mark.parametrize("args", [("id", None, None, None), ("id", "receipt", None, None),
                                 (None, "receipt", "profile", None), ("id", "receipt", "profile", "httpbin")])
def test_partial_or_combined_overrides_are_rejected(args):
    with pytest.raises(ValueError):
        p07.validate_dependency_options(*args)


def test_dependency_check_revalidates_fresh_container_source_and_metadata(tmp_path, monkeypatch):
    proof = admit(tmp_path, receipt(tmp_path))
    calls = []

    def command(args):
        calls.append(args)
        if "rev-parse" in args:
            return proof["source"]["head"]
        if "diff" in args or "status" in args:
            return ""
        assert args[3:5] == ["/opt/miniconda3/envs/testbed/bin/python", "-I"]
        return proof["pip_freeze"] if "freeze" in args else json.dumps(proof["pins"])

    monkeypatch.setattr(p07, "command", command)
    observed = p07.check_dependency_container("fresh-container", proof)
    assert observed["overlay_versions"] == proof["pins"]
    assert all(args[:4] == ["docker", "exec", "fresh-container", "git"] for args in calls[:3])


def test_stale_fresh_container_fails_before_metadata_or_target_execution(tmp_path, monkeypatch):
    proof = admit(tmp_path, receipt(tmp_path))
    calls = []

    def command(args):
        calls.append(args)
        assert "python" not in " ".join(args)
        return "0" * 40 if "rev-parse" in args else ""

    monkeypatch.setattr(p07, "command", command)
    with pytest.raises(ValueError, match="fresh dependency container source"):
        p07.check_dependency_container("fresh-container", proof)
    assert len(calls) == 3
