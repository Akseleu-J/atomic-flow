"""Explicit function36 root-worker execution, disconnected from generic dispatch.

C is the filtered candidate. A disposable resource packet combines its core
with the sole CA from the admitted image; E appends only the bound fixture CA.
Neither resource packet nor E is a publication candidate. All target code runs
inside the same bounded owned network-none worker used for fixture health.
"""
from __future__ import annotations

import hashlib
import os
import platform
import tempfile
from pathlib import Path

from atomic_flow import check_requests_execution as worker
from atomic_flow.agents.execution import ExecutionUnavailable
from atomic_flow.agents.source import confined_path
from atomic_flow.agents.step_evidence import manifest_digest
from atomic_flow.agents.verify import check_source_snapshot
from atomic_flow.check_execution import admit_check_spec
from atomic_flow.check_requests_execution_overlay import (
    prepare_requests_execution_overlay,
    validate_requests_execution_overlay,
)
from atomic_flow.check_requests_resources import CA_PATH, RequestsCAIdentity
from atomic_flow.check_spec import CheckSpec
from atomic_flow.config import ExecutionConfig
from atomic_flow.project_resources import ProjectLimits, require_disk_space, stream_source
from atomic_flow.source_lock import source_mutation_lease


def _candidate(spec, root, check):
    # A staged candidate never gains authority over the image-owned CA. Even an
    # identical inserted bundle is refused instead of silently ignored.
    if confined_path(root, CA_PATH).exists():
        raise ExecutionUnavailable("filtered function36 candidate must not contain a CA resource")
    snapshot = check_source_snapshot(root, check)
    if manifest_digest(snapshot) != spec.source_revision:
        raise ExecutionUnavailable("function36 candidate core source is stale")
    return snapshot


def _public_resources(spec, home, check):
    identity = spec.requests_ca_identity
    cidfile = home / "resource-container.cid"
    cid, attempted = None, False
    try:
        attempted = True
        worker._control([worker.ENGINE, "create", "--cidfile", str(cidfile), "--pull=never", "--network=none",
                         "--read-only", "--user=65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                         "--memory=1g", "--cpus=2", "--pids-limit=128", "--entrypoint=/bin/sleep",
                         "--", spec.image, "infinity"], home=home, check=check)
        cid = worker._cid(cidfile)
        inspected = worker._rows(worker._control([worker.ENGINE, "inspect", cid], home=home, check=check))
        h = inspected.get("HostConfig", {})
        if (inspected.get("Id") != cid or inspected.get("Image") != spec.image
                or inspected.get("Mounts") or h.get("Binds") or h.get("Privileged") is not False
                or h.get("ReadonlyRootfs") is not True or h.get("NetworkMode") != "none"
                or h.get("CapDrop") != ["ALL"] or h.get("CapAdd")
                or "no-new-privileges" not in h.get("SecurityOpt", [])
                or h.get("Memory") != 1024**3 or h.get("NanoCpus") != 2_000_000_000 or h.get("PidsLimit") != 128
                or inspected.get("Config", {}).get("User") != "65534:65534"
                or inspected.get("State", {}).get("Running") is not False):
            raise ExecutionUnavailable("immutable stopped public-resource container policy differs")
        require_disk_space(home, 2_000_000, ProjectLimits())
        values = []
        for index, (image_path, maximum) in enumerate((("/testbed/" + CA_PATH, 1_000_000),
                                                       ("/opt/p07-function-fixture/cacert.pem", 16384))):
            target = home / ("public-ca-" + str(index) + ".bin")
            worker._control([worker.ENGINE, "cp", cid + ":" + image_path, str(target)], home=home, check=check)
            if target.is_symlink() or not target.is_file() or not 1 <= target.stat().st_size <= maximum:
                raise ExecutionUnavailable("public CA resource is linked, missing or oversized")
            with target.open("rb") as stream:
                raw = stream.read(maximum + 1)
            if not 1 <= len(raw) <= maximum:
                raise ExecutionUnavailable("public CA resource changed during bounded read")
            values.append(raw)
        identity.validate_bytes(*values)
    finally:
        if cid is None and cidfile.exists():
            cid = worker._cid(cidfile)
        if cid is not None:
            removed = worker._remove_container(worker.ENGINE, cid, home)
            absent = worker._control([worker.ENGINE, "container", "ls", "-a", "--no-trunc", "--filter", "id=" + cid,
                                      "--format", "{{.ID}}"], home=home, check=lambda: None)
            if not removed or absent.strip():
                raise ExecutionUnavailable("public resource container cleanup is unresolved; container_id=" + cid)
        elif attempted:
            raise ExecutionUnavailable("public resource creation has no immutable ID; reconcile worker")
    return (*values, {"container_id": cid, "image_id": spec.image, "started": False, "cleanup_confirmed": True,
                      "original_ca_sha256": hashlib.sha256(values[0]).hexdigest(),
                      "fixture_ca_sha256": hashlib.sha256(values[1]).hexdigest()})


def _resource_packet(spec, candidate, destination, original, before, check):
    limits = ProjectLimits()
    reserve = sum(confined_path(candidate, relative).stat().st_size for relative in before) + len(original)
    if reserve > limits.max_total_bytes:
        raise ExecutionUnavailable("function36 resource packet exceeds source byte bound")
    require_disk_space(destination.parent, reserve * 2 + 16385, limits)
    destination.mkdir(mode=0o755)
    copied = 0
    for relative, expected in sorted(before.items()):
        check()
        source, target = confined_path(candidate, relative), confined_path(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        with target.open("xb") as sink:
            actual, size = stream_source(source, limits, check, sink=sink, remaining_bytes=limits.max_total_bytes - copied)
            sink.flush()
            os.fsync(sink.fileno())
        target.chmod(0o644 | (source.stat().st_mode & 0o111))
        if actual != expected:
            raise ExecutionUnavailable("function36 candidate changed during resource packet copy")
        copied += size
    path = confined_path(destination, CA_PATH)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    with path.open("xb") as stream:
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o644)
    if check_source_snapshot(destination, check) != before or _candidate(spec, candidate, check) != before:
        raise ExecutionUnavailable("function36 resource packet core differs from candidate")


def _run_function_check(spec: CheckSpec, candidate: Path, variant: str, *, cancel_check=None):
    """Return only after C/E/resource proofs, actual fixture stop and CID cleanup.

    Construction of a spec is not image admission. The caller must retain and
    independently verify its runtime, public CA and provisioning evidence.
    """
    if (type(spec) is not CheckSpec or type(spec.requests_ca_identity) is not RequestsCAIdentity
            or spec.fixture_identity is None or spec.fixture_identity.variant != variant
            or variant not in {"function36-v3", "2674-v2"}):
        raise ExecutionUnavailable("immutable resource-bound function36 specification required")
    if platform.system() != "Linux" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise ExecutionUnavailable("function36 requires an explicitly accepted root Linux worker")
    if cancel_check is not None and not callable(cancel_check):
        raise ExecutionUnavailable("host cancellation callback required")
    check = cancel_check or (lambda: None)
    candidate = Path(candidate).absolute()
    config = ExecutionConfig(mode="docker", profile="checks", image=spec.image,
                             output_bytes=spec.resources.output_bytes_per_stream)
    admit_check_spec(spec, candidate, config, spec.timeout_seconds, list(spec.argv[1:]), cancel_check=check)
    with source_mutation_lease(candidate), tempfile.TemporaryDirectory(prefix="atomic-function36-") as directory:
        home = Path(directory)
        home.chmod(0o755)
        before = _candidate(spec, candidate, check)
        original, fixture, provenance = _public_resources(spec, home, check)
        packet, execution = home / "resource-candidate", home / "execution"
        _resource_packet(spec, candidate, packet, original, before, check)
        execution_spec, overlay = prepare_requests_execution_overlay(
            spec, packet, execution, fixture, original_ca_sha256=spec.requests_ca_identity.original_ca_sha256, cancel_check=check)
        validate_requests_execution_overlay(spec, execution_spec, overlay, packet, execution, fixture,
            original_ca_sha256=spec.requests_ca_identity.original_ca_sha256, cancel_check=check)
        receipt, topology = worker._run_requests_check(execution_spec, execution, cancel_check=check, _function_overlay=overlay)
        validate_requests_execution_overlay(spec, execution_spec, overlay, packet, execution, fixture,
            original_ca_sha256=spec.requests_ca_identity.original_ca_sha256, cancel_check=check)
        if _candidate(spec, candidate, check) != before:
            raise ExecutionUnavailable("function36 candidate changed during actual execution")
        return receipt, topology, {"resource_container": provenance, "overlay": overlay.as_dict(),
                                   "candidate_ca_present": False, "candidate_core_revision": spec.source_revision,
                                   "execution_core_revision": execution_spec.source_revision,
                                   "candidate_and_execution_specs_equal": spec == execution_spec}


def run_function36_check(spec: CheckSpec, candidate: Path, *, cancel_check=None):
    return _run_function_check(spec, candidate, "function36-v3", cancel_check=cancel_check)


def run_requests2674_check(spec: CheckSpec, candidate: Path, *, cancel_check=None):
    return _run_function_check(spec, candidate, "2674-v2", cancel_check=cancel_check)
