"""Verify prospective publisher wheels without importing or installing them.

This produces a disposable wheelhouse and metadata receipt; it grants no image
or execution admission. A later worker must bind its actual base inventory and
qualify a derived immutable image independently.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import stat
import urllib.request
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "benchmarks/stage4/p07-pytest6116-dependencies-v1.json"
MAX_WHEEL = 16 * 1024 * 1024
ENVIRONMENT = {"python_version": "3.9", "python_full_version": "3.9.20", "implementation_name": "cpython",
               "implementation_version": "3.9.20", "os_name": "posix", "sys_platform": "linux",
               "platform_machine": "x86_64", "platform_python_implementation": "CPython", "extra": ""}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def inspect_wheel(raw: bytes, item: dict):
    if not 1 <= len(raw) <= MAX_WHEEL or sha(raw) != item["sha256"]:
        raise ValueError("wheel bytes differ from pinned publisher digest")
    with zipfile.ZipFile(io.BytesIO(raw)) as wheel:
        infos = wheel.infolist()
        names = [entry.filename for entry in infos if not entry.is_dir()]
        if len(names) != len(set(names)) or len(names) > 10000:
            raise ValueError("duplicate or excessive wheel members")
        for entry in infos:
            path = PurePosixPath(entry.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                    or stat.S_ISLNK(entry.external_attr >> 16) or entry.file_size > 4 * 1024 * 1024):
                raise ValueError("wheel member path or size is unsafe")
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        records = [name for name in names if name.endswith(".dist-info/RECORD")]
        if len(metadata) != 1 or len(records) != 1 or metadata[0].rsplit("/", 1)[0] != records[0].rsplit("/", 1)[0]:
            raise ValueError("wheel has ambiguous metadata or RECORD")
        headers = BytesParser().parsebytes(wheel.read(metadata[0]), headersonly=True)
        if canonicalize_name(headers["Name"]) != canonicalize_name(item["name"]) or headers["Version"] != item["version"]:
            raise ValueError("wheel metadata name or version differs")
        required_python = headers.get("Requires-Python")
        if required_python and Version(ENVIRONMENT["python_full_version"]) not in SpecifierSet(required_python):
            raise ValueError("wheel does not declare compatibility with target Python")
        entries = list(csv.reader(io.StringIO(wheel.read(records[0]).decode("utf8"))))
        if len(entries) != len(names) or {entry[0] for entry in entries} != set(names):
            raise ValueError("wheel RECORD does not cover every file exactly")
        for entry in entries:
            if len(entry) != 3:
                raise ValueError("malformed wheel RECORD")
            name, digest, length = entry
            content = wheel.read(name)
            if name == records[0]:
                if digest or length:
                    raise ValueError("wheel RECORD self-entry must be unhashed")
                continue
            expected = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
            if digest != expected or length != str(len(content)):
                raise ValueError("wheel member differs from RECORD")
        return {"name": canonicalize_name(item["name"]), "version": item["version"], "filename": item["filename"],
                "sha256": item["sha256"], "files": len(names), "requires_python": required_python,
                "requires_dist": headers.get_all("Requires-Dist", [])}


def prepare(output: Path):
    if output.exists():
        raise ValueError("fresh output directory required")
    profile_raw = PROFILE.read_bytes()
    profile = json.loads(profile_raw)
    if (profile.get("task_id") != "pytest-dev__pytest-6116" or profile.get("python_version") != "3.9.20"
            or len(profile.get("wheels", [])) != 3):
        raise ValueError("exact prospective profile required")
    output.mkdir(parents=True)
    inspected = []
    for item in profile["wheels"]:
        if not item["url"].startswith("https://files.pythonhosted.org/") or not item["url"].endswith("/" + item["filename"]):
            raise ValueError("only the pinned publisher wheel URL is admitted")
        request = urllib.request.Request(item["url"], headers={"User-Agent": "atomic-flow-p07-wheel-audit/1"})
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read(MAX_WHEEL + 1)
        inspected.append(inspect_wheel(raw, item))
        (output / item["filename"]).write_bytes(raw)
    versions = {canonicalize_name(name): Version(version) for name, version in profile["base_packages"].items()}
    versions.update({entry["name"]: Version(entry["version"]) for entry in inspected})
    edges = []
    for item in inspected:
        for text in item["requires_dist"]:
            requirement = Requirement(text)
            if requirement.marker and not requirement.marker.evaluate(ENVIRONMENT):
                continue
            name = canonicalize_name(requirement.name)
            if name not in versions or versions[name] not in requirement.specifier:
                raise ValueError("active wheel dependency is absent or incompatible: " + name)
            edges.append({"from": item["name"], "requirement": str(requirement), "resolved": str(versions[name])})
    receipt = {"status": "prospective_wheelhouse_verified_no_execution", "profile_sha256": sha(profile_raw),
               "target_python": profile["python_version"], "assumed_base_packages": profile["base_packages"],
               "wheels": inspected, "active_edges": edges, "target_imports": False,
               "image_built": False, "ordinary_acceptance": False, "provider_calls": 0}
    (output / "preparation.json").write_bytes((json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({"status": prepare(args.output)["status"]}))
