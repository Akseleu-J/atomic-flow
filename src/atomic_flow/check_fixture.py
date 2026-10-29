"""Pure host bindings for historical Requests dependencies, fixtures and CA overlays.

These contracts grant no execution, network, mount or publication authority.
Hashes establish byte consistency, not certificate validation or attestation.
Only disclosed benchmark public certificates are consumed; no key bytes are read.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from atomic_flow.agents.step_evidence import manifest_digest

# pytest2.8 writes cache/lastfailed during a completed failing session. Source
# stays read-only; disable this optional plugin in the sealed ordinary profile.
_PLUGIN_ARGS = ("-p", "no:cacheprovider", "-p", "no:httpbin", "-p", "pytest_httpbin.plugin", "-p", "pytest_mock", "-rA", "-v")
_ENTRYPOINTS = (("httpbin", "pytest_httpbin.plugin"), ("pytest_mock", "pytest_mock"))
_HELPERS = {
    "tools/p07_requests3362_calibrate.py": "862ed1fc8caed51fbcf71c0760bfdf0aa9ed3b765ddd1f29b6de44586e44a9ca",
    "tools/p07_requests3362_health.py": "a19f7354d9eb346509524b107c888addac6c74afebd227db5b8f3a2699acfe63",
    "tools/p07_requests_function_calibrate.py": "9947e8f714b456484c00f9c6800bbed9072c7fa5add648ba93206f4dc7a6d9aa",
    "tools/p07_requests_function_health.py": "44d98da47675938a9e26cc0ea6969406bc596c5f1eec7cdaaab34610e95f97fa",
    "tools/p07_requests2674_python27_calibrate.py": "45b584d1cbeceecc68f704a9926b9dcfb9b7f3e2ce050a304113a61b76555511",
    "tools/p07_requests2674_python27_health.py": "dffa7c77ee892a6af232156d09b9f07d4a9b917785817910a8971037e299740f",
    "tools/p07_requests863_python27_calibrate.py": "d78ff10c6fc919fb7a6e6b3a52213163cdc6a19be935f3bc71308ff382af7690",
    "tools/p07_requests863_python27_health.py": "c434086832feb05938e7478cdd74ee7579641e4bac9da012c679693cbdf06137",
    "tools/p07_requests863_service.py": "5bd82a6e8e98a704bf5b4be9c7a0dc7b1e7b0265af223461f0910bcc91cb7dc7",
    "tools/p07_requests863_wire_contracts.py": "885ea5c6a1f86d845fef39890782a3a06c994a6207e0fc116d0dd6d1484b59be",
    "tools/p07_timeout_topology_probe.py": "cd588bc733a84df04e218c1816bf0cd77e0fcc48d04d0ee09045e8d6012b5f98",
}
_VARIANTS = {
    "3362-cn": ((3, 6, 15), ("psf__requests-3362",), ("p07_requests3362_calibrate.py", "p07_requests3362_health.py")),
    "function36-v3": ((3, 6, 15), ("psf__requests-1963", "psf__requests-2148", "psf__requests-2317"),
                      ("p07_requests3362_calibrate.py", "p07_requests3362_health.py", "p07_requests_function_calibrate.py", "p07_requests_function_health.py")),
    "2674-v2": ((2, 7, 18), ("psf__requests-2674",), ("p07_requests2674_python27_calibrate.py", "p07_requests2674_python27_health.py")),
    "863-v7": ((2, 7, 13), ("psf__requests-863",), ("p07_requests863_python27_calibrate.py", "p07_requests863_python27_health.py",
                                                    "p07_requests863_service.py", "p07_requests863_wire_contracts.py")),
}
_PROFILES = {
    "3362-cn": "810e98b8f663ed03c3f4b529f692fdcdcffe46364ee678ac18fcda547ad1076f",
    "function36-v3": "2be37770e20e5379d6e1cbed7b67808308a7903fefb3ca70726c70b9280ecfde",
    "2674-v2": "61ed4c2119f1b93719297b54277f61293410b769ef5c090db7010177aa56a289",
    "863-v7": "93b6a88a99cf04c62984c2404af94bf647c139f4f9f294576ea7b3505126f31c",
}
_WIRE_HOSTS = ("httpbin.org", "github.com", "kennethreitz.com", "www.google.com", "blip.fm", "t.co", "tinyurl.com",
               "feedproxy.google.com", "www.dealipedia.com", "www.marketwire.com")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(name, value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(name + " requires an exact lowercase SHA256")


def _variant(variant, task_id):
    if type(variant) is not str or variant not in _VARIANTS or type(task_id) is not str or task_id not in _VARIANTS[variant][1]:
        raise ValueError("unreviewed task/fixture cohort")


def _pairs(value, *, absolute=False):
    if type(value) is not tuple or not 1 <= len(value) <= 5000:
        raise ValueError("immutable bounded file inventory required")
    paths = []
    for item in value:
        if type(item) is not tuple or len(item) != 2:
            raise ValueError("immutable path/hash pair required")
        path, digest = item
        if (type(path) is not str or len(path) > 512 or not re.fullmatch(r"/?[A-Za-z0-9_][A-Za-z0-9_./-]*", path)
                or path.startswith("/") != absolute
                or any(part in {"", ".", ".."} for part in path.lstrip("/").split("/"))):
            raise ValueError("plain confined inventory path required")
        _digest("inventory hash", digest)
        paths.append(path)
    if paths != sorted(set(paths)):
        raise ValueError("sorted unique inventory required")


def _helpers(variant, value):
    _pairs(value)
    expected_paths = sorted("tools/" + name for name in (*_VARIANTS[variant][2], "p07_timeout_topology_probe.py"))
    if value != tuple((path, _HELPERS[path]) for path in expected_paths):
        raise ValueError("helper inventory differs from reviewed exact cohort source bytes")


def reviewed_helper_inventory(variant: str) -> tuple[tuple[str, str], ...]:
    if type(variant) is not str or variant not in _VARIANTS:
        raise ValueError("unreviewed fixture variant")
    return tuple(sorted(("tools/" + name, _HELPERS["tools/" + name])
                        for name in (*_VARIANTS[variant][2], "p07_timeout_topology_probe.py")))


def _validate_bytes(inventory, supplied, *, maximum=4 * 1024**2):
    if type(supplied) is not dict or set(supplied) != {path for path, _ in inventory}:
        raise ValueError("exact complete retained byte inventory required")
    total = 0
    for path, expected in inventory:
        raw = supplied[path]
        if type(raw) is not bytes or len(raw) > maximum or _sha(raw) != expected:
            raise ValueError("retained bytes differ from admitted inventory")
        total += len(raw)
        if total > 128 * 1024**2:
            raise ValueError("retained inventory byte bound exceeded")


class _Canonical:
    __slots__ = ()

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")

    @property
    def digest(self) -> str:
        return _sha(self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class PrivatePytestIdentity(_Canonical):
    task_id: str
    variant: str
    runtime_digest: str
    python_version: tuple[int, int, int]
    installed_files: tuple[tuple[str, str], ...]
    helper_sha256: tuple[tuple[str, str], ...]
    pytest_version: str = "2.8.7"
    entrypoints: tuple[tuple[str, str], ...] = _ENTRYPOINTS
    plugin_args: tuple[str, ...] = _PLUGIN_ARGS

    def __post_init__(self):
        _variant(self.variant, self.task_id)
        _digest("runtime identity", self.runtime_digest)
        if (type(self.python_version) is not tuple or any(type(part) is not int for part in self.python_version)
                or self.python_version != _VARIANTS[self.variant][0] or type(self.pytest_version) is not str
                or self.pytest_version != "2.8.7" or self.entrypoints != _ENTRYPOINTS or self.plugin_args != _PLUGIN_ARGS):
            raise ValueError("unreviewed exact legacy pytest runtime/plugin profile")
        _pairs(self.installed_files)
        if any(path.endswith(("/key.pem", ".key")) or path == "key.pem" for path, _ in self.installed_files):
            raise ValueError("private pytest byte inventory excludes fixture key material; bind it in provisioning evidence")
        _helpers(self.variant, self.helper_sha256)
        if not {"pytest.py", "_pytest/__init__.py"} <= {path for path, _ in self.installed_files}:
            raise ValueError("private pytest entrypoint inventory missing")

    @property
    def site_packages(self) -> str:
        version = "python3.6" if self.python_version[0] == 3 else "python2.7"
        prefix = "/opt/p07-python36" if self.python_version[0] == 3 else "/opt/p07-python27"
        return prefix + "/lib/" + version + "/site-packages/"

    def as_dict(self):
        return {"schema_version": 1, "kind": "host_private_pytest_identity", "task_id": self.task_id, "variant": self.variant,
                "runtime_digest": self.runtime_digest, "python_version": list(self.python_version), "pytest_version": self.pytest_version,
                "site_packages": self.site_packages, "installed_files": dict(self.installed_files), "helper_sha256": dict(self.helper_sha256),
                "entrypoints": dict(self.entrypoints), "plugin_args": list(self.plugin_args)}

    def validate_bytes(self, *, installed_files_bytes: dict, helper_bytes: dict) -> str:
        _validate_bytes(self.installed_files, installed_files_bytes)
        _validate_bytes(self.helper_sha256, helper_bytes)
        return self.digest


@dataclass(frozen=True, slots=True)
class FixtureIdentity(_Canonical):
    task_id: str
    variant: str
    runtime_digest: str
    image_id: str
    profile_sha256: str
    helper_sha256: tuple[tuple[str, str], ...]
    certificate_files: tuple[tuple[str, str], ...]
    certificate_metadata_sha256: str
    health_policy_sha256: str

    def __post_init__(self):
        _variant(self.variant, self.task_id)
        for name in ("runtime_digest", "profile_sha256", "certificate_metadata_sha256", "health_policy_sha256"):
            _digest(name, getattr(self, name))
        if type(self.image_id) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_id) is None:
            raise ValueError("actual derived immutable image ID required")
        if self.variant in _PROFILES and self.profile_sha256 != _PROFILES[self.variant]:
            raise ValueError("fixture profile differs from reviewed exact variant")
        _helpers(self.variant, self.helper_sha256)
        _pairs(self.certificate_files, absolute=True)
        if {path for path, _ in self.certificate_files} != {self.certificate_root + name for name in ("cacert.pem", "cert.pem")}:
            raise ValueError("exact public fixture CA/server certificate inventory required")

    @property
    def certificate_root(self):
        if self.variant == "3362-cn":
            return "/opt/p07-python36/lib/python3.6/site-packages/pytest_httpbin/certs/"
        return "/opt/p07-function-fixture/"

    @property
    def host_mappings(self):
        if self.variant == "3362-cn":
            return ()
        hosts = _WIRE_HOSTS if self.variant == "863-v7" else ("httpbin.org",)
        if self.task_id in {"psf__requests-2148", "psf__requests-2317", "psf__requests-2674"}:
            hosts = (*hosts, "www.google.co.uk")
        return tuple(sorted((host, "127.0.0.1") for host in hosts))

    @property
    def certificate_policy(self):
        return {"common_name": "127.0.0.1" if self.variant == "3362-cn" else "httpbin.org",
                "dns_sans": [] if self.variant == "3362-cn" else sorted(["httpbin.org", "github.com"] if self.variant == "863-v7" else ["httpbin.org"]),
                "ip_sans": []}

    def as_dict(self):
        return {"schema_version": 1, "kind": "host_requests_fixture_identity", "task_id": self.task_id, "variant": self.variant,
                "runtime_digest": self.runtime_digest, "image_id": self.image_id, "profile_sha256": self.profile_sha256,
                "helper_sha256": dict(self.helper_sha256), "certificate_files": dict(self.certificate_files),
                "certificate_metadata_sha256": self.certificate_metadata_sha256, "health_policy_sha256": self.health_policy_sha256,
                "certificate_policy": self.certificate_policy, "host_mappings": dict(self.host_mappings),
                "ports": [] if self.variant == "3362-cn" else [80, 443],
                "urls": {} if self.variant == "3362-cn" else {"http": "http://httpbin.org/", "https": "https://httpbin.org/"},
                "topology_policy": "owned-netns-loopback-tcp80-drop/1", "certificate_assurance": "metadata_and_bytes_integrity_only"}

    def validate_bytes(self, *, profile_bytes: bytes, helper_bytes: dict, public_certificate_bytes: dict,
                       certificate_metadata_bytes: bytes, health_policy_bytes: bytes) -> str:
        _validate_bytes((("profile", self.profile_sha256),), {"profile": profile_bytes})
        _validate_bytes(self.helper_sha256, helper_bytes)
        _validate_bytes(self.certificate_files, public_certificate_bytes, maximum=16384)
        _validate_bytes((("metadata", self.certificate_metadata_sha256),), {"metadata": certificate_metadata_bytes}, maximum=32768)
        _validate_bytes((("health", self.health_policy_sha256),), {"health": health_policy_bytes}, maximum=32768)
        for raw in public_certificate_bytes.values():
            if not raw.startswith(b"-----BEGIN CERTIFICATE-----") or b"PRIVATE KEY" in raw:
                raise ValueError("only disclosed public certificate bytes accepted")
        try:
            metadata = json.loads(certificate_metadata_bytes)
        except (ValueError, TypeError) as error:
            raise ValueError("certificate metadata must be bounded JSON") from error
        if metadata != self.certificate_policy:
            raise ValueError("certificate metadata differs from fixed cohort policy")
        return self.digest


@dataclass(frozen=True, slots=True)
class ExecutionOverlay(_Canonical):
    task_id: str
    candidate_revision: str
    execution_revision: str
    fixture_digest: str
    runtime_digest: str
    check_spec_digest: str
    original_ca_sha256: str
    fixture_ca_sha256: str
    result_ca_sha256: str
    before_manifest_sha256: str
    after_manifest_sha256: str
    candidate_core_revision: str
    execution_core_revision: str
    path: str = "requests/cacert.pem"
    operation: str = "append_fixture_ca"

    def __post_init__(self):
        if type(self.task_id) is not str or self.task_id not in {"psf__requests-1963", "psf__requests-2148", "psf__requests-2317", "psf__requests-2674", "psf__requests-863"}:
            raise ValueError("task has no reviewed CA append overlay")
        for name in self.__dataclass_fields__:
            if name not in {"task_id", "path", "operation"}:
                _digest(name, getattr(self, name))
        if (type(self.path) is not str or type(self.operation) is not str
                or self.path != "requests/cacert.pem" or self.operation != "append_fixture_ca"):
            raise ValueError("only the fixed Requests CA append is admitted")
        if (self.candidate_revision != self.before_manifest_sha256 or self.execution_revision != self.after_manifest_sha256
                or self.candidate_revision == self.execution_revision):
            raise ValueError("distinct candidate/execution revisions must match their source manifests")
        if self.candidate_core_revision != self.execution_core_revision:
            raise ValueError("CA overlay must preserve the core source revision")

    def as_dict(self):
        return {"schema_version": 2, "kind": "host_requests_execution_overlay", **{
            name: getattr(self, name) for name in self.__dataclass_fields__},
            "source_manifest_policy": "core_plus_fixed_requests_ca_sha1_v1", "hidden_mount": False}

    def validate_bytes(self, *, original: bytes, fixture_ca: bytes, result: bytes,
                       before_manifest: dict, after_manifest: dict) -> str:
        for name, raw, expected in (("original", original, self.original_ca_sha256), ("fixture", fixture_ca, self.fixture_ca_sha256),
                                    ("result", result, self.result_ca_sha256)):
            if type(raw) is not bytes or not 1 <= len(raw) <= 1_000_000 or _sha(raw) != expected:
                raise ValueError(name + " CA bytes differ from overlay binding")
        if (not fixture_ca.startswith(b"-----BEGIN CERTIFICATE-----") or b"PRIVATE KEY" in fixture_ca
                or result != original + b"\n" + fixture_ca):
            raise ValueError("execution CA must be exactly original bytes plus disclosed fixture CA")
        if (type(before_manifest) is not dict or type(after_manifest) is not dict
                or set(before_manifest) != set(after_manifest) or self.path not in before_manifest):
            raise ValueError("overlay must preserve the complete candidate file inventory")
        # PEM stays outside core source admission. These resource manifests add
        # exactly the fixed CA path; removing it reconstructs the core manifest.
        if (before_manifest[self.path] != hashlib.sha1(original).hexdigest()
                or after_manifest[self.path] != hashlib.sha1(result).hexdigest()
                or any(before_manifest[path] != after_manifest[path] for path in before_manifest if path != self.path)
                or manifest_digest(before_manifest) != self.candidate_revision
                or manifest_digest(after_manifest) != self.execution_revision):
            raise ValueError("execution overlay changed unrelated source or candidate/execution manifest identity")
        if (manifest_digest({p: h for p, h in before_manifest.items() if p != self.path}) != self.candidate_core_revision
                or manifest_digest({p: h for p, h in after_manifest.items() if p != self.path}) != self.execution_core_revision):
            raise ValueError("resource manifest differs from the separately bound core source revision")
        return self.digest
