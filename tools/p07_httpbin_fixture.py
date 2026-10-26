"""Readiness audit for a real loopback HTTPBin fixture, never a fake HTTP server.

This tool does not install packages, enable network access, mutate target tests,
disable TLS verification or import target code. An image without the real pinned
fixture distributions must stay unavailable rather than silently emulate them.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import ipaddress
import json
import re
import signal
import socket
import ssl
import sys
import threading
import urllib.request
from pathlib import Path

BASE_PACKAGES = frozenset({"httpbin", "flask", "werkzeug"})
PLUGIN_PACKAGE = "pytest-httpbin"


def package_name(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def freeze_versions(text):
    """A direct URL without a version is not a verified dependency pin."""
    result = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if "==" in line:
            name, version = line.split("==", 1)
            result[package_name(name)] = version
        elif " @ " in line:
            name, _ = line.split(" @ ", 1)
            result[package_name(name)] = None
    return result


def readiness(installed, expected=None, *, require_plugin=False):
    installed = {package_name(name): value for name, value in installed.items()}
    expected = {package_name(name): value for name, value in (expected or {}).items()}
    required = BASE_PACKAGES | ({PLUGIN_PACKAGE} if require_plugin else set())
    packages = {}
    for name in sorted(required):
        actual, declared = installed.get(name), expected.get(name)
        if name not in installed:
            state = "missing_distribution"
        elif not actual:
            state = "installed_version_unverified"
        elif not declared:
            state = "reviewed_pin_missing"
        elif actual != declared:
            state = "version_mismatch"
        else:
            state = "exact_version_present"
        packages[name] = {"status": state, "installed_version": actual, "reviewed_version": declared}
    all_present = all(value["status"] == "exact_version_present" for value in packages.values())
    return {"status": "packages_ready_runtime_fixture_not_started" if all_present else "unavailable",
            "ready_to_start_real_fixture": all_present, "fixture_started": False, "packages": packages,
            "network_contract": "none; bind HTTP/HTTPS to loopback only; no external proxy",
            "tls_contract": "reviewed test CA and httpbin.org SAN certificate; never verify=False",
            "hardcoded_hostname_contract": "httpbin.org must resolve to container loopback for HTTP80/HTTPS443",
            "plugin_contract": "load only explicit pytest_httpbin.plugin after exact package pin is verified" if require_plugin else "not_required",
            "unsupported_fallbacks": ["internet_access", "unversioned_pip_install", "fake_httpbin_semantics", "test_mutation", "tls_verification_bypass"],
            "model_calls": 0}


def installed_versions():
    result = {}
    for name in BASE_PACKAGES | {PLUGIN_PACKAGE}:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return result


def verify_serve_lock(lock, certificate, key):
    """Check all resolved distributions and build-generated test certificate bytes."""
    if lock.get("schema_version") != 1 or not isinstance(lock.get("packages"), dict):
        raise ValueError("versioned fixture package lock required")
    packages = lock["packages"]
    if not BASE_PACKAGES <= {package_name(name) for name in packages}:
        raise ValueError("lock omits the real HTTPBin/Flask/Werkzeug distributions")
    versions = {}
    for name, expected in packages.items():
        if not isinstance(expected, str) or not expected:
            raise ValueError("fixture dependency has no exact version")
        versions[name] = importlib.metadata.version(name)
        if versions[name] != expected:
            raise ValueError("fixture distribution version mismatch: " + name)
    if not lock.get("wheel_hashes") or any(not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in lock["wheel_hashes"].values()):
        raise ValueError("reviewed dependency wheel hash manifest required")
    for name, path in (("certificate_sha256", certificate), ("test_key_sha256", key)):
        if hashlib.sha256(path.read_bytes()).hexdigest() != lock.get(name):
            raise ValueError("reviewed test TLS fixture hash mismatch: " + name)
    return versions


def serve(lock, certificate, key, output):
    """Serve the actual pinned HTTPBin application in its separate fixture venv.

    Build/download and certificate generation happen before target execution.
    This entrypoint never downloads packages or contacts an external endpoint.
    """
    versions = verify_serve_lock(lock, certificate, key)
    fixture_prefix = Path(lock.get("fixture_venv", ""))
    if not fixture_prefix.is_absolute() or Path(sys.prefix).resolve() != fixture_prefix.resolve():
        raise ValueError("HTTPBin must run in its separately locked fixture venv")
    source_hashes = lock.get("fixture_sources_sha256", {})
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != source_hashes.get(Path(__file__).name):
        raise ValueError("fixture lifecycle source differs from reviewed image source")
    mappings = lock.get("host_mappings")
    if not isinstance(mappings, dict) or mappings.get("httpbin.org") != "127.0.0.1":
        raise ValueError("explicit locked loopback host mappings required")
    resolved = {}
    for host, expected in mappings.items():
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        if not addresses or addresses != {expected} or not ipaddress.ip_address(expected).is_loopback:
            raise ValueError("fixture hostname must resolve exclusively to its locked loopback address: " + host)
        resolved[host] = sorted(addresses)
    import httpbin
    import werkzeug
    from werkzeug.serving import make_server

    for module in (httpbin, werkzeug):
        if not module.__file__ or not Path(module.__file__).resolve().is_relative_to(fixture_prefix.resolve()):
            raise ValueError("fixture application imported outside its locked venv")
    app = httpbin.app

    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate, key)
    http = make_server("127.0.0.1", 80, app, threaded=True)
    try:
        https = make_server("127.0.0.1", 443, app, threaded=True, ssl_context=tls)
    except BaseException:
        http.server_close()
        raise
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *unused: stopped.set())
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (http, https)]
    for thread in threads:
        thread.start()
    try:
        # These are real HTTPBin endpoints; neither response content nor test
        # semantics is implemented by this host-owned lifecycle wrapper.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certificate)))
        probes = {}
        for scheme in ("http", "https"):
            with opener.open(scheme + "://httpbin.org/status/204", timeout=5) as response:
                if response.status != 204:
                    raise ValueError("real HTTPBin health probe failed")
                probes[scheme] = response.status
        receipt = {"status": "real_httpbin_started", "bind": ["127.0.0.1:80", "127.0.0.1:443"],
            "resolved_addresses": resolved, "packages": versions, "probes": probes,
            "certificate_sha256": lock["certificate_sha256"], "test_key_role": "public benchmark test fixture; never a host credential",
            "fixture_venv": str(fixture_prefix),
            "fixture_sources_sha256": source_hashes,
            "application_module_paths": {module.__name__: module.__file__ for module in (httpbin, werkzeug)},
            "network_requirement": "container must independently prove network=none", "model_calls": 0}
        if output.exists():
            raise ValueError("fresh fixture readiness receipt required")
        output.write_text(json.dumps(receipt, indent=2) + "\n")
        while not stopped.wait(1):
            if any(not thread.is_alive() for thread in threads):
                raise RuntimeError("HTTPBin service thread stopped unexpectedly")
    finally:
        for server in (http, https):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-freeze", type=Path, help="Read a downloaded container pip-freeze as data instead of this host")
    parser.add_argument("--expected-lock", type=Path, help="Reviewed name -> exact version mapping; absent pins fail closed")
    parser.add_argument("--require-plugin", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--serve", action="store_true", help="Start real pinned HTTPBin in the isolated fixture venv, never target Python")
    parser.add_argument("--certificate", type=Path)
    parser.add_argument("--test-key", type=Path)
    args = parser.parse_args()
    if args.serve:
        if not all((args.expected_lock, args.certificate, args.test_key, args.output)) or args.installed_freeze:
            raise ValueError("serve requires lock, reviewed test certificate/key and fresh output; no downloaded freeze substitution")
        serve(json.loads(args.expected_lock.read_text()), args.certificate, args.test_key, args.output)
        return 0
    actual = freeze_versions(args.installed_freeze.read_text()) if args.installed_freeze else installed_versions()
    expected = json.loads(args.expected_lock.read_text()) if args.expected_lock else None
    if expected and "packages" in expected:
        expected = expected["packages"]
    report = readiness(actual, expected, require_plugin=args.require_plugin)
    report["observation_source"] = str(args.installed_freeze) if args.installed_freeze else "current_process_distribution_metadata"
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        if args.output.exists():
            raise ValueError("fresh readiness report required")
        args.output.write_text(encoded)
    print(encoded)
    return 0 if report["ready_to_start_real_fixture"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
