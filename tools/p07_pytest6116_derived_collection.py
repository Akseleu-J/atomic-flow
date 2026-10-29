"""Qualify a wheel-bound derived image and observe public pytest collection.

No host imports target packages; Docker build installs only verified wheels with
network disabled. Collection still executes public conftest/import code inside
the bounded container and does not constitute ordinary-test acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import p07_pytest6116_collection_recon as recon
import p07_pytest6116_prepare_dependencies as wheels

ENGINE = "/usr/bin/docker"
PREFIX = "/opt/p07-pytest6116-deps"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def expected_wheel_files(wheelhouse: Path, profile: dict):
    expected = {}
    for item in profile["wheels"]:
        raw = (wheelhouse / item["filename"]).read_bytes()
        wheels.inspect_wheel(raw, item)
        with zipfile.ZipFile(wheelhouse / item["filename"]) as archive:
            for entry in archive.infolist():
                if entry.is_dir() or entry.filename.endswith(".dist-info/RECORD"):
                    continue
                if entry.filename in expected:
                    raise ValueError("wheel files overlap")
                expected[entry.filename] = sha(archive.read(entry))
    return expected


def installed_files(root: Path):
    files = {}
    for parent, directories, names in os.walk(root, followlinks=False):
        for name in [*directories, *names]:
            file = Path(parent) / name
            if file.is_symlink():
                raise ValueError("installed dependency contains a link")
        for name in names:
            file = Path(parent) / name
            if not file.is_file() or file.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("installed dependency file unavailable or oversized")
            files[file.relative_to(root).as_posix()] = sha(file.read_bytes())
    return files


def validate_installed(expected, actual):
    if any(actual.get(name) != digest for name, digest in expected.items()):
        raise ValueError("installed dependency differs from publisher wheel bytes")
    extras = set(actual) - set(expected)
    dist_dirs = {name.rsplit("/", 1)[0] for name in expected if name.endswith(".dist-info/METADATA")}
    if not dist_dirs or any(directory + "/RECORD" not in actual for directory in dist_dirs):
        raise ValueError("installed dependency RECORD missing")
    permitted = {directory + "/" + suffix for directory in dist_dirs
                 for suffix in ("RECORD", "INSTALLER", "REQUESTED", "direct_url.json")}
    if extras - permitted:
        raise ValueError("unexpected installed dependency files: " + repr(sorted(extras - permitted)))
    return sorted(extras)


def run(output: Path):
    if output.exists() or os.name != "posix":
        raise ValueError("fresh output and Linux Docker host required")
    output.mkdir(parents=True)
    profile_raw = wheels.PROFILE.read_bytes()
    profile = json.loads(profile_raw)
    report = {"status": "not_qualified", "scope": "derived image and public collection only", "provider_calls": 0,
              "ordinary_acceptance": False, "profile_sha256": sha(profile_raw)}
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": "/tmp"}

    def command(args, timeout=120):
        return subprocess.run(args, env=env, capture_output=True, timeout=timeout, check=False)

    with tempfile.TemporaryDirectory(prefix="p07-pytest-derived-") as temporary:
        temp = Path(temporary)
        wheelhouse = temp / "wheelhouse"
        cidfile = temp / "deps.cid"
        cid = None
        try:
            prepared = wheels.prepare(wheelhouse)
            (output / "wheel-preparation.json").write_bytes((wheelhouse / "preparation.json").read_bytes())
            if prepared["profile_sha256"] != sha(profile_raw):
                raise ValueError("wheel preparation profile differs")
            expected = expected_wheel_files(wheelhouse, profile)
            base_pull = command([ENGINE, "pull", recon.IMAGE], timeout=240)
            (output / "base-pull.stdout").write_bytes(base_pull.stdout)
            (output / "base-pull.stderr").write_bytes(base_pull.stderr)
            if base_pull.returncode:
                raise ValueError("pinned base image unavailable")
            base_result = command([ENGINE, "image", "inspect", recon.IMAGE])
            if base_result.returncode:
                raise ValueError("base image inspection failed")
            base = json.loads(base_result.stdout)[0]
            if recon.IMAGE not in base.get("RepoDigests", []) or base.get("Os") != "linux" or base.get("Architecture") != "amd64":
                raise ValueError("base image identity differs")
            context = temp / "context"
            context.mkdir()
            for item in profile["wheels"]:
                shutil.copyfile(wheelhouse / item["filename"], context / item["filename"])
            dockerfile = ("FROM " + recon.IMAGE + "\nWORKDIR /tmp\nCOPY *.whl /opt/p07-wheels/\n"
                "RUN --network=none /opt/miniconda3/envs/testbed/bin/python -I -B -m pip --isolated install "
                "--no-index --no-deps --no-compile --disable-pip-version-check --target " + PREFIX
                + " /opt/p07-wheels/*.whl && chmod -R a+rX " + PREFIX + "\n"
                "LABEL atomic.p07.pytest6116.profile=" + sha(profile_raw) + "\n")
            (context / "Dockerfile").write_text(dockerfile, encoding="ascii")
            (output / "Dockerfile").write_text(dockerfile, encoding="ascii")
            iid = temp / "image.id"
            build = command([ENGINE, "build", "--pull=false", "--network=none", "--iidfile", str(iid), str(context)], timeout=300)
            (output / "build.stdout").write_bytes(build.stdout)
            (output / "build.stderr").write_bytes(build.stderr)
            if build.returncode or not iid.is_file():
                raise ValueError("offline derived-image build failed")
            image_id = iid.read_text(encoding="ascii").strip()
            if re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None:
                raise ValueError("derived immutable image ID missing")
            inspected = command([ENGINE, "image", "inspect", image_id])
            derived = json.loads(inspected.stdout)[0] if not inspected.returncode else {}
            layers = base.get("RootFS", {}).get("Layers", [])
            actual_layers = derived.get("RootFS", {}).get("Layers", [])
            if (derived.get("Id") != image_id or not layers or actual_layers[:len(layers)] != layers
                    or len(actual_layers) <= len(layers)
                    or derived.get("Config", {}).get("Labels", {}).get("atomic.p07.pytest6116.profile") != sha(profile_raw)):
                raise ValueError("derived image ancestry or profile binding differs")
            (output / "base-inspect.json").write_bytes(base_result.stdout)
            (output / "image-inspect.json").write_bytes(inspected.stdout)
            created = command([ENGINE, "create", "--cidfile", str(cidfile), "--pull=never", "--network=none", "--read-only",
                "--user=65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=1g",
                "--cpus=2", "--pids-limit=128", "--entrypoint=/bin/sleep", image_id, "infinity"])
            if created.returncode:
                raise ValueError("dependency inspection container creation failed")
            cid = cidfile.read_text(encoding="ascii").strip()
            if re.fullmatch(r"[0-9a-f]{64}", cid) is None:
                raise ValueError("dependency inspection CID missing")
            container = command([ENGINE, "inspect", cid])
            source = json.loads(container.stdout)[0] if not container.returncode else {}
            host = source.get("HostConfig", {})
            if (source.get("Id") != cid or source.get("Image") != image_id or source.get("Mounts")
                    or host.get("Binds") or host.get("Privileged") is not False
                    or host.get("ReadonlyRootfs") is not True or host.get("NetworkMode") != "none"
                    or host.get("CapDrop") != ["ALL"] or host.get("CapAdd")
                    or "no-new-privileges" not in host.get("SecurityOpt", [])
                    or source.get("State", {}).get("Running") is not False):
                raise ValueError("stopped dependency inspection container differs")
            (output / "dependency-container-inspect.json").write_bytes(container.stdout)
            copied = command([ENGINE, "cp", cid + ":" + PREFIX, str(temp / "installed")])
            if copied.returncode:
                raise ValueError("dependency inventory extraction failed")
            actual = installed_files(temp / "installed")
            extras = validate_installed(expected, actual)
            (output / "installed-inventory.json").write_bytes((json.dumps(actual, sort_keys=True, indent=2) + "\n").encode())
            started = command([ENGINE, "start", cid])
            if started.returncode:
                raise ValueError("dependency inventory container failed to start")
            attrs = command([ENGINE, "exec", "--user=65534:65534", "-w", "/tmp", cid, recon.PYTHON,
                             "-I", "-B", "-c", "import importlib.metadata as m; print(m.version('attrs'))"])
            (output / "base-attrs.stdout").write_bytes(attrs.stdout)
            (output / "base-attrs.stderr").write_bytes(attrs.stderr)
            if attrs.returncode or attrs.stdout.strip() != b"23.1.0":
                raise ValueError("actual base attrs differs from prospective closure")
            removed = command([ENGINE, "rm", "-f", cid])
            absent = command([ENGINE, "container", "ls", "-a", "--no-trunc", "--filter", "id=" + cid, "--format", "{{.ID}}"])
            if removed.returncode or absent.returncode or absent.stdout.strip():
                raise ValueError("dependency inspection container cleanup unresolved: " + cid)
            report.update(image_id=image_id, base_image_id=base["Id"], expected_files=len(expected),
                          installed_files=len(actual), pip_metadata_files=extras, dependency_container_cleanup=True)
            cid = None
            observed = recon.run(output / "collection", admitted_image=image_id, dependency_prefix=PREFIX)
            dependencies = json.loads((output / "collection/dependencies.stdout").read_bytes())
            if [row for row in dependencies if row[0].lower() == "attrs"] != [["attrs", "23.1.0"]]:
                raise ValueError("actual base attrs inventory differs from prospective closure")
            report["collection_status"] = observed["status"]
            report["status"] = "derived_collection_observed" if observed["status"] == "collection_observed" else "derived_collection_incomplete"
        except Exception as error:
            report.update(error_type=type(error).__name__, error=str(error)[:500])
        finally:
            if cid is None and cidfile.exists() and not report.get("dependency_container_cleanup"):
                cid = cidfile.read_text(encoding="ascii").strip()
            if cid and re.fullmatch(r"[0-9a-f]{64}", cid):
                removed = command([ENGINE, "rm", "-f", cid])
                absent = command([ENGINE, "container", "ls", "-a", "--no-trunc", "--filter", "id=" + cid, "--format", "{{.ID}}"])
                report["dependency_container_cleanup"] = not removed.returncode and not absent.returncode and not absent.stdout.strip()
                if not report["dependency_container_cleanup"]:
                    report["status"] = "cleanup_uncertain"
    (output / "summary.json").write_bytes((json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    raise SystemExit(0 if run(options.output)["status"] == "derived_collection_observed" else 1)
