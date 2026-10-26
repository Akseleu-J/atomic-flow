"""Prepare a wheel-hashed real HTTPBin fixture layer; never execute target code.

The resulting Docker build performs offline fixture-only installation. Neither
this preparer nor the Dockerfile runs target setup/tests or installs into target
requests. Build the resulting image on an approved Linux Docker worker.
"""
from __future__ import annotations

import argparse
import email
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename

# Explicit wheel-only CPython3.9 profile. Flasgger0.9.7.1 has no published wheel;
# flasgger0.9.5 uses the Flask2/mistune0 interface, so those are pinned together.
PINS = {
    "httpbin": "0.10.2", "pytest-httpbin": "2.1.0", "Flask": "2.2.5", "Werkzeug": "2.2.3",
    "flasgger": "0.9.5", "mistune": "0.8.4", "Jinja2": "3.1.6", "MarkupSafe": "2.1.5",
    "itsdangerous": "2.2.0", "click": "8.1.8", "PyYAML": "6.0.2", "jsonschema": "4.23.0",
    "attrs": "25.3.0", "jsonschema-specifications": "2025.4.1", "referencing": "0.36.2",
    "rpds-py": "0.27.1", "typing-extensions": "4.14.1", "six": "1.17.0", "decorator": "5.2.1",
    "brotlicffi": "1.1.0.0", "cffi": "1.17.1", "pycparser": "2.22", "greenlet": "2.0.2",
    "importlib-metadata": "8.7.0", "zipp": "3.23.0",
}
TARGET_ENV = {"implementation_name": "cpython", "implementation_version": "3.9.20", "os_name": "posix",
    "platform_machine": "x86_64", "platform_release": "", "platform_system": "Linux", "platform_version": "",
    "python_full_version": "3.9.20", "platform_python_implementation": "CPython", "python_version": "3.9",
    "sys_platform": "linux", "extra": ""}

# This test asserts only credentials removed across a different hostname. The
# destination response content/status is not asserted. The original hostname
# remains in the real redirect URL; only its isolated transport resolves locally.
OFF_HOST_3362 = {
    "instance_id": "psf__requests-3362", "base_commit": "36453b95b13079296776d11b09cab2567ea3e703",
    "source_url": "https://raw.githubusercontent.com/psf/requests/36453b95b13079296776d11b09cab2567ea3e703/tests/test_requests.py",
    "source_sha256": "a6fbbc48cf42501a3a3490a1b225377a60448e309fdeb3a09e09ba6ac53d8b82",
    "test": "TestRequests.test_auth_is_stripped_on_redirect_off_host",
    "test_source_sha256": "5113876355989d0c2a0b9aba45941cc5ee6c1f7b733f618ff9eac243ae380428",
    "original_destination": "http://www.google.co.uk",
    "assertions": ["r.history[0].request.headers['Authorization']", "not r.request.headers.get('Authorization', '')"],
    "mapping": {"www.google.co.uk": "127.0.0.1"},
    "rationale": "Preserve real different-host redirect/auth removal with real HTTPBin as destination transport; no claim of contacting Google or reproducing Google response content",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def json_write(path, value):
    path.write_bytes((json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def validate_wheels(wheel_dir, pins, fetch_json):
    normalized = {canonicalize_name(name): version for name, version in pins.items()}
    wheels, requirements = {}, []
    for wheel in sorted(wheel_dir.glob("*.whl")):
        name, version, _, _ = parse_wheel_filename(wheel.name)
        name = canonicalize_name(name)
        if name not in normalized or str(version) != normalized[name] or name in wheels:
            raise ValueError("unexpected, duplicate or unpinned wheel: " + wheel.name)
        metadata = fetch_json("https://pypi.org/pypi/" + name + "/" + str(version) + "/json")
        published = next((file for file in metadata["urls"] if file["filename"] == wheel.name and file["packagetype"] == "bdist_wheel"), None)
        digest = sha(wheel.read_bytes())
        if not published or published["digests"]["sha256"] != digest:
            raise ValueError("wheel bytes do not match primary PyPI digest: " + wheel.name)
        with zipfile.ZipFile(wheel) as archive:
            names = [path for path in archive.namelist() if path.endswith(".dist-info/METADATA")]
            if len(names) != 1:
                raise ValueError("wheel metadata identity ambiguous")
            message = email.message_from_bytes(archive.read(names[0]))
            if canonicalize_name(message["Name"]) != name or message["Version"] != str(version):
                raise ValueError("wheel metadata package identity mismatch")
            for declared in message.get_all("Requires-Dist", []):
                requirement = Requirement(declared)
                if requirement.marker and not requirement.marker.evaluate(TARGET_ENV):
                    continue
                dependency = canonicalize_name(requirement.name)
                if requirement.url or requirement.extras:
                    raise ValueError("explicit review needed for dependency URL/extra: " + declared)
                if dependency not in normalized or normalized[dependency] not in requirement.specifier:
                    raise ValueError("incomplete or incompatible fixture dependency closure: " + name + " -> " + declared)
        wheels[name] = {"version": str(version), "filename": wheel.name, "sha256": digest, "pypi_url": published["url"],
                        "primary_metadata_url": "https://pypi.org/pypi/" + name + "/" + str(version) + "/json"}
        requirements.append(f"{name}=={version} --hash=sha256:{digest}")
    if set(wheels) != set(normalized):
        raise ValueError("missing locked wheels: " + str(sorted(set(normalized) - set(wheels))))
    return wheels, requirements


def prepare(output, image, *, download=True, wheel_source=None):
    if not re.fullmatch(r"swebench/[A-Za-z0-9_.-]+@sha256:[0-9a-f]{64}", image):
        raise ValueError("official immutable SWE-bench image digest required")
    if output.exists():
        raise ValueError("fresh image preparation directory required")
    output.mkdir(parents=True)
    wheels_dir = output / "wheels"
    wheels_dir.mkdir()
    json_write(output / "declared-pins.json", PINS)
    if wheel_source is not None:
        for wheel in Path(wheel_source).glob("*.whl"):
            if wheel.is_symlink() or not wheel.is_file():
                raise ValueError("wheel reuse requires ordinary files; all bytes are reverified")
            shutil.copyfile(wheel, wheels_dir / wheel.name)
    if download and wheel_source is None:
        environment = {key: value for key, value in os.environ.items() if key.upper() in {
            "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME"}}
        command = [sys.executable, "-m", "pip", "--isolated", "download", "--index-url", "https://pypi.org/simple",
            "--disable-pip-version-check", "--no-cache-dir", "--only-binary=:all:", "--no-deps", "--platform", "manylinux2014_x86_64",
            "--python-version", "39", "--implementation", "cp", "--abi", "cp39", "--dest", str(wheels_dir),
            *[name + "==" + version for name, version in sorted(PINS.items())]]
        with (output / "download.log").open("wb") as stream:
            subprocess.run(command, env=environment, stdout=stream, stderr=subprocess.STDOUT, timeout=600, check=True)

    def fetch(url):
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.load(response)

    wheels, requirements = validate_wheels(wheels_dir, PINS, fetch)
    (output / "requirements.lock").write_bytes(("\n".join(requirements) + "\n").encode("utf-8"))
    tools = Path(__file__).resolve().parent
    sources = {}
    for name in ("p07_httpbin_fixture.py", "p07_httpbin_pytest.py"):
        raw = (tools / name).read_bytes()
        shutil.copyfile(tools / name, output / name)
        sources[name] = sha(raw)
    lock = {"schema_version": 1, "base_image": image, "packages": {name: data["version"] for name, data in wheels.items()},
            "wheel_hashes": {data["filename"]: data["sha256"] for data in wheels.values()}, "wheels": wheels,
            "fixture_sources_sha256": sources, "target_environment": TARGET_ENV,
            "fixture_venv": "/opt/p07-httpbin",
            "certificate_role": "public benchmark fixture generated inside image build; never a user/host credential"}
    lock["host_mappings"] = {"httpbin.org": "127.0.0.1"}
    if ".psf_1776_requests-3362@" in image:
        lock["host_mappings"].update(OFF_HOST_3362["mapping"])
        lock["transport_mapping_audit"] = OFF_HOST_3362
    json_write(output / "fixture-lock.build.json", lock)
    finalize = '''import hashlib,json,pathlib,shutil,sysconfig
root=pathlib.Path('/opt/p07-fixture')
lock=json.loads((root/'fixture-lock.build.json').read_text())
for key,name in [('certificate_sha256','httpbin-cert.pem'),('test_key_sha256','httpbin-key.pem')]:
    lock[key]=hashlib.sha256((root/name).read_bytes()).hexdigest()
(root/'fixture-lock.json').write_text(json.dumps(lock,indent=2)+'\\n')
plugin=root/'p07_httpbin_pytest.py'
assert hashlib.sha256(plugin.read_bytes()).hexdigest()==lock['fixture_sources_sha256'][plugin.name]
shutil.copyfile(plugin,pathlib.Path(sysconfig.get_paths()['purelib'])/plugin.name)
'''
    compile(finalize, "finalize_fixture.py", "exec")
    (output / "finalize_fixture.py").write_bytes(finalize.encode("utf-8"))
    dockerfile = f'''FROM {image}
USER root
COPY wheels/ /opt/p07-fixture/wheels/
COPY requirements.lock fixture-lock.build.json p07_httpbin_fixture.py p07_httpbin_pytest.py finalize_fixture.py /opt/p07-fixture/
RUN --network=none /opt/miniconda3/envs/testbed/bin/python -m venv /opt/p07-httpbin && /opt/p07-httpbin/bin/python -m pip install --no-index --require-hashes --find-links=/opt/p07-fixture/wheels -r /opt/p07-fixture/requirements.lock
RUN --network=none openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj /CN=httpbin.org -addext subjectAltName=DNS:httpbin.org,DNS:localhost,IP:127.0.0.1 -addext basicConstraints=critical,CA:TRUE -keyout /opt/p07-fixture/httpbin-key.pem -out /opt/p07-fixture/httpbin-cert.pem && chmod 644 /opt/p07-fixture/httpbin-key.pem && /opt/miniconda3/envs/testbed/bin/python /opt/p07-fixture/finalize_fixture.py
'''
    (output / "Dockerfile").write_bytes(dockerfile.encode("utf-8"))
    receipt = {"status": "prepared_not_built", "base_image": image, "wheel_count": len(wheels), "model_calls": 0,
        "dockerfile_sha256": sha(dockerfile.encode()), "dependency_lock_sha256": sha((output / "requirements.lock").read_bytes()),
        "fixture_sources_sha256": sources, "fixture_runner": "/opt/p07-httpbin/bin/python /opt/p07-fixture/p07_httpbin_fixture.py --serve --expected-lock /opt/p07-fixture/fixture-lock.json --certificate /opt/p07-fixture/httpbin-cert.pem --test-key /opt/p07-fixture/httpbin-key.pem --output /tmp/p07-httpbin-ready.json",
        "docker_runtime_additions": ["--network", "none", "--sysctl", "net.ipv4.ip_unprivileged_port_start=0",
            *[part for host, address in sorted(lock["host_mappings"].items()) for part in ("--add-host", host + ":" + address)]],
        "target_pytest_plugin": "p07_httpbin_pytest (host-owned adapter, not upstream pytest_httpbin)",
        "target_environment": {"HTTPBIN_URL": "http://httpbin.org/", "REQUESTS_CA_BUNDLE": "/opt/p07-fixture/httpbin-cert.pem",
            "CURL_CA_BUNDLE": "/opt/p07-fixture/httpbin-cert.pem", "PYTEST_PLUGINS": "p07_httpbin_pytest"},
        "target_requests_installation": "unchanged; fixture dependencies use separate venv",
        "runtime_healthcheck": "real HTTPBin /status/204 over HTTP and CA-verified HTTPS required before target test admission"}
    json_write(output / "preparation.json", receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wheel-source", type=Path, help="Reuse downloaded wheel bytes, then reverify every PyPI digest and dependency edge")
    print(json.dumps(prepare(**vars(parser.parse_args())), indent=2))
