"""Prepare the sole admitted function36 CA overlay in a fresh host snapshot.

No target code, process or executor is invoked. Resource identity adds only the
fixed public CA bundle to the filtered core manifest. Core revisions remain
separate and unchanged by the CA append; no global PEM admission is enabled.
The caller must independently admit runtime/fixture provenance, the upstream
CA hash, and ownership of the destination parent. A source mutation lease
coordinates cooperating writers; this is not an OS-atomic snapshot or exclusion
of external writers. Failed preparation retains its partial destination.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from atomic_flow.agents.source import SourceConflict, confined_path
from atomic_flow.agents.step_evidence import manifest_digest
from atomic_flow.agents.verify import check_source_snapshot
from atomic_flow.check_fixture import ExecutionOverlay, FixtureIdentity, PrivatePytestIdentity
from atomic_flow.check_runtime import RuntimeIdentity
from atomic_flow.check_spec import CheckSpec
from atomic_flow.project_resources import ProjectLimits, require_disk_space, source_files, stream_source
from atomic_flow.source_lock import source_mutation_lease

CA_PATH = "requests/cacert.pem"
_TASKS = frozenset({"psf__requests-1963", "psf__requests-2148", "psf__requests-2317", "psf__requests-2674"})
_MAX_CA_BYTES = 1_000_000
_MAX_FIXTURE_CA_BYTES = 16_384


def _check_callback(cancel_check):
    if cancel_check is not None and not callable(cancel_check):
        raise ValueError("host cancellation callback must be callable")
    return cancel_check or (lambda: None)


def _plain(path: Path) -> Path:
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise SourceConflict("execution overlay ownership path contains a link")
    return path


def _candidate_root(candidate: Path) -> Path:
    root = _plain(candidate)
    if not root.is_dir():
        raise SourceConflict("execution overlay candidate must be an existing directory")
    return root.resolve(strict=True)


def _separate_path(candidate: Path, execution: Path, *, fresh: bool) -> Path:
    path = _plain(execution)
    if not path.parent.is_dir():
        raise SourceConflict("execution overlay parent must be an existing owned directory")
    resolved = path.parent.resolve(strict=True) / path.name
    if resolved.is_relative_to(candidate) or candidate.is_relative_to(resolved):
        raise SourceConflict("execution overlay destination must be outside candidate source")
    if fresh and path.exists():
        raise SourceConflict("execution overlay destination must be fresh")
    if not fresh and not path.is_dir():
        raise SourceConflict("execution overlay snapshot must be an existing directory")
    return resolved


def _binding(spec: CheckSpec, fixture_ca: bytes, original_ca_sha256: str) -> None:
    if type(spec) is not CheckSpec:
        raise ValueError("immutable candidate CheckSpec required")
    runtime, identity, fixture = spec.runtime_identity, spec.private_pytest_identity, spec.fixture_identity
    if (spec.task_id not in _TASKS or spec.runner != "pytest"
            or type(runtime) is not RuntimeIdentity or type(identity) is not PrivatePytestIdentity
            or type(fixture) is not FixtureIdentity
            or (identity.variant, fixture.variant, runtime.python_version) not in {
                ("function36-v3", "function36-v3", (3, 6, 15)),
                ("2674-v2", "2674-v2", (2, 7, 18)),
            }
            or identity.python_version != runtime.python_version or runtime.runner != spec.runner
            or identity.task_id != spec.task_id or fixture.task_id != spec.task_id
            or identity.runtime_digest != runtime.digest or fixture.runtime_digest != runtime.digest
            or fixture.image_id != runtime.image_id or spec.image != runtime.image_id
            or spec.interpreter != runtime.interpreter):
        raise ValueError("exact admitted private function36 task/runtime/fixture metadata required")
    runtime.require_execution_eligible()
    if (type(original_ca_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", original_ca_sha256) is None):
        raise ValueError("independently admitted original CA SHA256 required")
    if (type(fixture_ca) is not bytes or not 1 <= len(fixture_ca) <= _MAX_FIXTURE_CA_BYTES
            or not fixture_ca.startswith(b"-----BEGIN CERTIFICATE-----") or b"PRIVATE KEY" in fixture_ca
            or hashlib.sha256(fixture_ca).hexdigest()
            != dict(fixture.certificate_files)[fixture.certificate_root + "cacert.pem"]):
        raise ValueError("public fixture CA bytes differ from their exact admitted identity")


def _read_ca(root: Path, manifest: dict, original_ca_sha256: str, check) -> bytes:
    if CA_PATH not in manifest:
        raise SourceConflict("admitted original Requests CA bundle is missing")
    sink = BytesIO()
    fingerprint, _ = stream_source(confined_path(root, CA_PATH), ProjectLimits(), check,
                                   sink=sink, remaining_bytes=_MAX_CA_BYTES)
    raw = sink.getvalue()
    if (not raw or fingerprint != manifest[CA_PATH]
            or hashlib.sha256(raw).hexdigest() != original_ca_sha256):
        raise SourceConflict("original CA bytes differ from independently admitted upstream bundle")
    return raw


def _resource_snapshot(root: Path, check) -> dict:
    """Core source plus exactly one bounded, non-linked CA resource."""
    manifest = check_source_snapshot(root, check)
    if CA_PATH in manifest:
        raise SourceConflict("fixed CA resource unexpectedly admitted as core source")
    path = confined_path(root, CA_PATH)
    if not path.is_file():
        raise SourceConflict("admitted original Requests CA bundle is missing")
    fingerprint, _ = stream_source(path, ProjectLimits(), check, remaining_bytes=_MAX_CA_BYTES)
    return manifest | {CA_PATH: fingerprint}


def _fresh_candidate(spec: CheckSpec, root: Path, check) -> dict:
    if manifest_digest(check_source_snapshot(root, check)) != spec.source_revision:
        raise SourceConflict("candidate CheckSpec source revision is stale")
    return _resource_snapshot(root, check)


def _execution_paths(root: Path, expected: set[str], check) -> set[str]:
    """E contains only copied admitted files; even ignored additions refuse."""
    limits = ProjectLimits()
    directories = {parent.as_posix() for relative in expected for parent in Path(relative).parents
                   if parent != Path(".")}

    def skip(relative, is_dir):
        mode = confined_path(root, relative).stat(follow_symlinks=False).st_mode
        if not (stat.S_ISDIR(mode) if is_dir else stat.S_ISREG(mode)):
            raise SourceConflict("execution overlay contains a nonregular entry")
        if is_dir and relative not in directories:
            raise SourceConflict("execution snapshot contains an unadmitted directory")
        return False

    files = set()
    for relative, _ in source_files(root, skip, limits, check):
        if len(files) >= limits.max_files:
            raise SourceConflict("execution overlay file count limit exceeded")
        files.add(relative)
    return files


def _validate(candidate_spec, execution_spec, overlay, candidate, execution, fixture_ca,
              original_ca_sha256, check):
    _binding(candidate_spec, fixture_ca, original_ca_sha256)
    if type(execution_spec) is not CheckSpec or type(overlay) is not ExecutionOverlay:
        raise ValueError("immutable execution CheckSpec and typed overlay required")
    if replace(candidate_spec, source_revision=execution_spec.source_revision) != execution_spec:
        raise ValueError("execution CheckSpec may change only the source revision")
    before = _fresh_candidate(candidate_spec, candidate, check)
    original = _read_ca(candidate, before, original_ca_sha256, check)
    after = _resource_snapshot(execution, check)
    if _execution_paths(execution, set(after), check) != set(after):
        raise SourceConflict("execution snapshot contains unadmitted or ignored extra files")
    expected_after = dict(before)
    combined = original + b"\n" + fixture_ca
    if len(combined) > _MAX_CA_BYTES:
        raise ValueError("combined execution CA bundle exceeds its admitted byte bound")
    expected_after[CA_PATH] = hashlib.sha1(combined).hexdigest()
    execution_core = manifest_digest(check_source_snapshot(execution, check))
    if after != expected_after or execution_core != execution_spec.source_revision:
        raise SourceConflict("execution snapshot changed unrelated source or exact CA append bytes")
    expected = ExecutionOverlay(
        candidate_spec.task_id, manifest_digest(before), manifest_digest(after),
        candidate_spec.fixture_identity.digest, candidate_spec.runtime_identity.digest, execution_spec.digest,
        original_ca_sha256, hashlib.sha256(fixture_ca).hexdigest(), hashlib.sha256(combined).hexdigest(),
        manifest_digest(before), manifest_digest(after), candidate_spec.source_revision, execution_core)
    if overlay != expected:
        raise ValueError("execution overlay differs from its candidate/spec/runtime/fixture binding")
    result = _read_ca(execution, after, hashlib.sha256(combined).hexdigest(), check)
    overlay.validate_bytes(original=original, fixture_ca=fixture_ca, result=result,
                           before_manifest=before, after_manifest=after)
    if _fresh_candidate(candidate_spec, candidate, check) != before:
        raise SourceConflict("candidate changed during execution overlay validation")
    if _resource_snapshot(execution, check) != after:
        raise SourceConflict("execution snapshot changed during overlay validation")
    if _execution_paths(execution, set(after), check) != set(after):
        raise SourceConflict("execution snapshot inventory changed during overlay validation")
    return overlay.digest


def validate_requests_execution_overlay(candidate_spec: CheckSpec, execution_spec: CheckSpec,
                                        overlay: ExecutionOverlay, candidate: Path, execution: Path,
                                        fixture_ca: bytes, *, original_ca_sha256: str,
                                        cancel_check=None) -> str:
    """Reconstruct exact E from current C; this does not validate test completion.

    The caller exclusively owns E. Apart from the fixed public CA resource,
    ignored/private C paths are not copied and
    extra files in E, including ignored ones, refuse. Filesystem permissions
    and ignored candidate files are outside the admitted source revision.
    """
    check = _check_callback(cancel_check)
    check()
    _binding(candidate_spec, fixture_ca, original_ca_sha256)
    candidate = _candidate_root(candidate)
    execution = _separate_path(candidate, execution, fresh=False)
    with source_mutation_lease(candidate):
        return _validate(candidate_spec, execution_spec, overlay, candidate, execution, fixture_ca,
                         original_ca_sha256, check)


def prepare_requests_execution_overlay(candidate_spec: CheckSpec, candidate: Path, destination: Path,
                                       fixture_ca: bytes, *, original_ca_sha256: str,
                                       cancel_check=None) -> tuple[CheckSpec, ExecutionOverlay]:
    """Copy admitted C to fresh E and append only the exact public fixture CA.

    No existing destination is modified, removed or reused. A failed attempt
    leaves its partial fresh directory for explicit caller reconciliation.
    Independently admitted original-CA bytes forbid silently overlaying an
    edited upstream CA bundle. The original candidate remains publication C.
    """
    check = _check_callback(cancel_check)
    check()
    _binding(candidate_spec, fixture_ca, original_ca_sha256)
    candidate = _candidate_root(candidate)
    destination = _separate_path(candidate, destination, fresh=True)
    limits = ProjectLimits()
    with source_mutation_lease(candidate):
        before = _fresh_candidate(candidate_spec, candidate, check)
        original = _read_ca(candidate, before, original_ca_sha256, check)
        combined = original + b"\n" + fixture_ca
        if len(combined) > _MAX_CA_BYTES:
            raise ValueError("combined execution CA bundle exceeds its admitted byte bound")
        reserve = sum(confined_path(candidate, relative).stat().st_size for relative in before) + len(fixture_ca) + 1
        if reserve > limits.max_total_bytes:
            raise ValueError("execution overlay would exceed the core total source byte limit")
        require_disk_space(destination.parent, reserve, limits)
        check()
        destination.mkdir(mode=0o755)
        destination.chmod(0o755)
        copied = 0
        for relative, expected in sorted(before.items()):
            check()
            source = confined_path(candidate, relative)
            target = confined_path(destination, relative)
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            for directory in (target.parent, *target.parent.parents):
                if directory == destination:
                    break
                directory.chmod(0o755)
            with target.open("xb") as sink:
                if relative == CA_PATH:
                    sink.write(combined)
                    size = len(combined)
                else:
                    actual, size = stream_source(source, limits, check, sink=sink,
                                                 remaining_bytes=limits.max_total_bytes - copied)
                    if actual != expected:
                        raise SourceConflict("candidate changed while copying execution snapshot")
                sink.flush()
                os.fsync(sink.fileno())
            target.chmod(0o644 | (source.stat().st_mode & 0o111))
            expected_copy = hashlib.sha1(combined).hexdigest() if relative == CA_PATH else expected
            actual_copy, actual_size = stream_source(target, limits, check)
            if actual_copy != expected_copy or actual_size != size:
                raise SourceConflict("copied execution bytes differ from admitted source")
            copied += size
        after = dict(before)
        after[CA_PATH] = hashlib.sha1(combined).hexdigest()
        # The CA is not core source. This spec alone grants no execution
        # authority: the separate resource receipt is required by future wiring.
        execution_spec = replace(candidate_spec, source_revision=manifest_digest(
            {path: digest for path, digest in after.items() if path != CA_PATH}))
        overlay = ExecutionOverlay(
            candidate_spec.task_id, manifest_digest(before), manifest_digest(after),
            candidate_spec.fixture_identity.digest, candidate_spec.runtime_identity.digest, execution_spec.digest,
            original_ca_sha256, hashlib.sha256(fixture_ca).hexdigest(), hashlib.sha256(combined).hexdigest(),
            manifest_digest(before), manifest_digest(after), candidate_spec.source_revision, execution_spec.source_revision)
        _validate(candidate_spec, execution_spec, overlay, candidate, destination, fixture_ca,
                  original_ca_sha256, check)
        return execution_spec, overlay
