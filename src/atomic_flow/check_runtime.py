"""Host-owned runtime bindings; byte/inspect consistency is not attestation.

No loader from model input, subprocess, shell, environment or network controls
are exposed here. The host must verify provisioning evidence independently
before constructing an identity and retain the corresponding exact bytes.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

_IMAGE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*@sha256:[0-9a-f]{64}")
_LAYOUTS = {
    (2, 7, 13): ("/opt/p07-python27/bin/python2.7", "/opt/p07-python27", "/opt/p07-runtime/python27"),
    (2, 7, 18): ("/opt/p07-python27/bin/python2.7", "/opt/p07-python27", "/opt/p07-runtime/python27"),
    (3, 6, 15): ("/opt/p07-python36/bin/python3.6", "/opt/p07-python36", "/opt/p07-runtime/python36"),
    (3, 9, 20): ("/opt/miniconda3/envs/testbed/bin/python", "", ""),
}


def _sha(name: str, value: str, prefix: str = "") -> None:
    if type(value) is not str or re.fullmatch(re.escape(prefix) + r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{name} must be an exact lowercase SHA256 digest")


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    """Exact reviewed image/layout/evidence binding, granting no execution rights.

    interpreter is the observed CPython executable; launcher identifies the
    reviewed private-runtime entry point, never an arbitrary command string.
    The helper hash binds the reviewed bootstrap/materializer bytes separately
    from the profile, dependency inventory and provisioning receipt hashes.
    """

    base_image: str
    base_image_id: str
    image_id: str
    interpreter: str
    python_version: tuple[int, int, int]
    runner: str
    profile_sha256: str
    helper_sha256: str
    inventory_sha256: str
    provisioning_receipt_sha256: str
    private_prefix: str = ""
    launcher: str = ""

    def __post_init__(self) -> None:
        if type(self.base_image) is not str or len(self.base_image) > 512 or _IMAGE.fullmatch(self.base_image) is None:
            raise ValueError("base_image must be an exact image name at SHA256 digest")
        for name in ("base_image_id", "image_id"):
            _sha(name, getattr(self, name), "sha256:")
        for name in ("profile_sha256", "helper_sha256", "inventory_sha256", "provisioning_receipt_sha256"):
            _sha(name, getattr(self, name))
        version = self.python_version
        if (type(version) is not tuple or len(version) != 3
                or any(type(part) is not int for part in version) or version not in _LAYOUTS):
            raise ValueError("unreviewed exact Python version")
        layout = (self.interpreter, self.private_prefix, self.launcher)
        if any(type(part) is not str for part in layout) or layout != _LAYOUTS[version]:
            raise ValueError("unreviewed runtime executable/private layout")
        if type(self.runner) is not str or self.runner not in {"pytest", "sympy-native"}:
            raise ValueError("unreviewed runner family")
        if self.runner == "sympy-native" and version[:2] == (2, 7):
            raise ValueError("Python2 native SymPy has no reviewed runner")

    def as_dict(self) -> dict:
        value = {name: getattr(self, name) for name in self.__dataclass_fields__}
        value["python_version"] = list(self.python_version)
        return {"schema_version": 1, "kind": "host_runtime_identity", **value}

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class RuntimeRegistry:
    """An immutable host-created selection set, never loaded from model text."""

    identities: tuple[RuntimeIdentity, ...]

    def __post_init__(self) -> None:
        if (type(self.identities) is not tuple or not 1 <= len(self.identities) <= 256
                or any(type(item) is not RuntimeIdentity for item in self.identities)):
            raise ValueError("registry must contain a bounded immutable host identity tuple")
        if len({item.digest for item in self.identities}) != len(self.identities):
            raise ValueError("duplicate runtime identities")

    def resolve(self, digest: str) -> RuntimeIdentity:
        _sha("runtime identity", digest)
        for identity in self.identities:
            if identity.digest == digest:
                return identity
        raise ValueError("runtime identity is not host registered")


def _inspect(value: dict, image_id: str) -> tuple[str, ...]:
    if type(value) is not dict or value.get("Id") != image_id:
        raise ValueError("Docker inspect image ID mismatch")
    if value.get("Os") != "linux" or value.get("Architecture") != "amd64":
        raise ValueError("runtime requires Linux amd64 image")
    root = value.get("RootFS")
    if type(root) is not dict or root.get("Type") != "layers":
        raise ValueError("Docker inspect requires actual RootFS layers")
    layers = root.get("Layers")
    if type(layers) is not list or not 1 <= len(layers) <= 256:
        raise ValueError("Docker inspect layer inventory is missing or unbounded")
    for layer in layers:
        _sha("RootFS layer", layer, "sha256:")
    return tuple(layers)


def validate_runtime_admission(
    identity: RuntimeIdentity, *, base_inspect: dict, image_inspect: dict,
    profile_bytes: bytes, helper_bytes: bytes, inventory_bytes: bytes,
    provisioning_receipt_bytes: bytes,
) -> str:
    """Check host-collected bytes and image metadata; return the binding digest.

    The caller supplies Docker image-inspect *objects* fetched by immutable ID
    and exact base digest. RepoTags and Docker's optional Parent are ignored.
    Matching rootfs prefixes prove metadata consistency, not build provenance,
    runtime probe success, receipt authenticity, or independent oracle success.
    """
    if type(identity) is not RuntimeIdentity:
        raise ValueError("runtime identity must be the immutable host contract")
    for name, raw in (("profile", profile_bytes), ("helper", helper_bytes),
                      ("inventory", inventory_bytes), ("provisioning_receipt", provisioning_receipt_bytes)):
        if type(raw) is not bytes or not 1 <= len(raw) <= 16 * 1024**2:
            raise ValueError(f"{name} evidence must be nonempty bounded exact bytes")
        if hashlib.sha256(raw).hexdigest() != getattr(identity, name + "_sha256"):
            raise ValueError(f"{name} evidence hash mismatch")
    base_layers = _inspect(base_inspect, identity.base_image_id)
    image_layers = _inspect(image_inspect, identity.image_id)
    refs = base_inspect.get("RepoDigests")
    if (type(refs) is not list or len(refs) > 256
            or any(type(ref) is not str or len(ref) > 512 for ref in refs)
            or identity.base_image not in refs):
        raise ValueError("base image digest is not bound by Docker inspect")
    if identity.image_id == identity.base_image_id:
        if image_layers != base_layers:
            raise ValueError("identical image IDs have inconsistent layers")
    elif len(image_layers) <= len(base_layers) or image_layers[:len(base_layers)] != base_layers:
        raise ValueError("derived runtime does not preserve the exact base RootFS prefix")
    return identity.digest
