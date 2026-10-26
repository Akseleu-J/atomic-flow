"""Explicit target-pytest fixture adapter for a separate REAL HTTPBin service.

Audited interface: requests3362 tests/conftest.py needs the upstream object's
``.url`` and constructs URL strings itself. Endpoint semantics are entirely the
unmodified, locked httpbin package in /opt/p07-httpbin; no network/API mocks live
here. This adapter is not represented as upstream pytest_httpbin being loaded.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path("/opt/p07-fixture")


def fixture_receipt():
    lock = json.loads((ROOT / "fixture-lock.json").read_text())
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != lock["fixture_sources_sha256"]["p07_httpbin_pytest.py"]:
        raise RuntimeError("host HTTPBin fixture adapter source differs from reviewed image source")
    ready = json.loads(Path("/tmp/p07-httpbin-ready.json").read_text())
    if ready.get("status") != "real_httpbin_started" or ready.get("probes") != {"http": 204, "https": 204}:
        raise RuntimeError("real HTTPBin health receipt absent or incomplete")
    cert = ROOT / "httpbin-cert.pem"
    if (hashlib.sha256(cert.read_bytes()).hexdigest() != ready.get("certificate_sha256")
            or ready.get("certificate_sha256") != lock.get("certificate_sha256")
            or ready.get("fixture_sources_sha256") != lock["fixture_sources_sha256"]
            or ready.get("fixture_venv") != lock.get("fixture_venv")):
        raise RuntimeError("HTTPBin CA fixture identity mismatch")
    if os.environ.get("REQUESTS_CA_BUNDLE") != str(cert):
        raise RuntimeError("target client must trust the explicit benchmark test CA")
    return ready


@pytest.fixture(scope="session")
def httpbin():
    fixture_receipt()
    return SimpleNamespace(url="http://httpbin.org/", host="127.0.0.1", port=80)


@pytest.fixture(scope="session")
def httpbin_secure():
    fixture_receipt()
    return SimpleNamespace(url="https://httpbin.org/", host="127.0.0.1", port=443)


@pytest.fixture
def httpbin_ca_bundle(monkeypatch):
    fixture_receipt()
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(ROOT / "httpbin-cert.pem"))
