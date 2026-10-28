"""Pure host runtime admission tests: no Docker or target code is executed."""
import hashlib
import json
from dataclasses import FrozenInstanceError, replace

import pytest

from atomic_flow.check_runtime import RuntimeIdentity, RuntimeRegistry, validate_runtime_admission


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _identity():
    return RuntimeIdentity(
        base_image="example/target@sha256:" + "a" * 64,
        base_image_id="sha256:" + "b" * 64, image_id="sha256:" + "c" * 64,
        interpreter="/opt/p07-python36/bin/python3.6", python_version=(3, 6, 15),
        runner="sympy-native", private_prefix="/opt/p07-python36", launcher="/opt/p07-runtime/python36",
        profile_sha256=_sha(b"profile"), helper_sha256=_sha(b"helper"),
        inventory_sha256=_sha(b"inventory"), provisioning_receipt_sha256=_sha(b"receipt"),
    )


def _evidence(identity):
    base = {"Id": identity.base_image_id, "RepoDigests": [identity.base_image],
            "Os": "linux", "Architecture": "amd64",
            "RootFS": {"Type": "layers", "Layers": ["sha256:" + "d" * 64]}}
    derived = {"Id": identity.image_id, "Os": "linux", "Architecture": "amd64",
               "RootFS": {"Type": "layers", "Layers": ["sha256:" + "d" * 64, "sha256:" + "e" * 64]}}
    return dict(base_inspect=base, image_inspect=derived, profile_bytes=b"profile", helper_bytes=b"helper",
                inventory_bytes=b"inventory", provisioning_receipt_bytes=b"receipt")


def test_identity_is_immutable_canonical_and_registered_by_complete_digest():
    identity = _identity()
    assert json.loads(identity.canonical_bytes()) == identity.as_dict()
    assert identity.digest == _sha(identity.canonical_bytes())
    assert RuntimeRegistry((identity,)).resolve(identity.digest) is identity
    with pytest.raises(FrozenInstanceError):
        identity.image_id = "sha256:" + "f" * 64
    for field in ("profile_sha256", "helper_sha256", "inventory_sha256", "provisioning_receipt_sha256"):
        changed = replace(identity, **{field: "f" * 64})
        assert changed.digest != identity.digest
        with pytest.raises(ValueError, match="not host registered"):
            RuntimeRegistry((identity,)).resolve(changed.digest)


@pytest.mark.parametrize("changes", [
    {"base_image": "example/target:latest"}, {"image_id": "sha256:" + "C" * 64},
    {"base_image_id": "sha256:bad"}, {"profile_sha256": "a" * 63},
    {"python_version": [3, 6, 15]}, {"python_version": (3, 6, True)},
    {"python_version": (3, 6, 14)}, {"interpreter": "/tmp/python"},
    {"interpreter": "/opt/p07-python36/bin/../bin/python3.6"},
    {"launcher": "/bin/sh -c python"}, {"private_prefix": "/opt/p07-python36/"},
    {"runner": "shell"}, {"helper_sha256": None}, {"inventory_sha256": "A" * 64},
    {"provisioning_receipt_sha256": b"a" * 64},
])
def test_identity_rejects_unreviewed_or_mutable_fields(changes):
    with pytest.raises(ValueError):
        replace(_identity(), **changes)


@pytest.mark.parametrize("version", [(2, 7, 13), (2, 7, 18)])
def test_reviewed_python27_paths_require_exact_version_and_pytest(version):
    identity = replace(_identity(), python_version=version, runner="pytest",
                       interpreter="/opt/p07-python27/bin/python2.7",
                       launcher="/opt/p07-runtime/python27", private_prefix="/opt/p07-python27")
    assert validate_runtime_admission(identity, **_evidence(identity)) == identity.digest
    with pytest.raises(ValueError, match="Python2"):
        replace(identity, runner="sympy-native")


def test_reviewed_conda_runtime_has_no_invented_private_launcher():
    identity = replace(_identity(), python_version=(3, 9, 20), runner="pytest",
                       interpreter="/opt/miniconda3/envs/testbed/bin/python", private_prefix="", launcher="")
    assert validate_runtime_admission(identity, **_evidence(identity)) == identity.digest


def test_inspect_and_exact_evidence_bind_the_complete_identity():
    identity = _identity()
    assert validate_runtime_admission(identity, **_evidence(identity)) == identity.digest


@pytest.mark.parametrize("field", ["profile_bytes", "helper_bytes", "inventory_bytes", "provisioning_receipt_bytes"])
@pytest.mark.parametrize("raw", [b"changed", b"", "profile", bytearray(b"profile")])
def test_evidence_requires_exact_immutable_bytes(field, raw):
    identity = _identity()
    evidence = _evidence(identity)
    evidence[field] = raw
    with pytest.raises(ValueError):
        validate_runtime_admission(identity, **evidence)


@pytest.mark.parametrize("mutation", [
    "base_id", "derived_id", "base_digest", "tag_only", "missing_layers", "empty_layers",
    "different_prefix", "short_layers", "invalid_layer", "wrong_os", "wrong_arch", "wrong_rootfs_type",
])
def test_inspect_rejects_unbound_images_and_ancestry(mutation):
    identity = _identity()
    evidence = _evidence(identity)
    base, derived = evidence["base_inspect"], evidence["image_inspect"]
    if mutation == "base_id":
        base["Id"] = "sha256:" + "f" * 64
    elif mutation == "derived_id":
        derived["Id"] = "sha256:" + "f" * 64
    elif mutation == "base_digest":
        base["RepoDigests"] = ["other/target@sha256:" + "a" * 64]
    elif mutation == "tag_only":
        base.pop("RepoDigests")
        base["RepoTags"] = [identity.base_image]
    elif mutation == "missing_layers":
        derived.pop("RootFS")
    elif mutation == "empty_layers":
        base["RootFS"]["Layers"] = []
    elif mutation == "different_prefix":
        derived["RootFS"]["Layers"][0] = "sha256:" + "f" * 64
    elif mutation == "short_layers":
        derived["RootFS"]["Layers"] = base["RootFS"]["Layers"][:]
    elif mutation == "invalid_layer":
        derived["RootFS"]["Layers"].append("sha256:short")
    elif mutation == "wrong_os":
        derived["Os"] = "windows"
    elif mutation == "wrong_arch":
        derived["Architecture"] = "arm64"
    elif mutation == "wrong_rootfs_type":
        derived["RootFS"]["Type"] = "other"
    with pytest.raises(ValueError):
        validate_runtime_admission(identity, **evidence)


def test_registry_rejects_mutability_duplicates_and_arbitrary_selection():
    identity = _identity()
    for entries in ([], (), [identity], (identity, identity), (identity.as_dict(),)):
        with pytest.raises(ValueError):
            RuntimeRegistry(entries)
    with pytest.raises(ValueError):
        RuntimeRegistry((identity,)).resolve("sha256:" + identity.digest)


def test_same_image_requires_identical_rootfs_metadata():
    identity = replace(_identity(), image_id=_identity().base_image_id)
    evidence = _evidence(identity)
    with pytest.raises(ValueError, match="inconsistent"):
        validate_runtime_admission(identity, **evidence)
    evidence["image_inspect"]["RootFS"] = evidence["base_inspect"]["RootFS"]
    assert validate_runtime_admission(identity, **evidence) == identity.digest
