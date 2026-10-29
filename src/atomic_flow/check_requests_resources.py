"""Fixed public CA resources for private function36 checks; no execution grant.

The original bundle comes from the admitted immutable image, outside the
filtered candidate. Only the public wrong-CA fixture is serialized as data.
No key, arbitrary path, command, environment override or mount is admitted.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from atomic_flow.check_fixture import _Canonical, _digest

FUNCTION36_TASKS = frozenset({"psf__requests-1963", "psf__requests-2148", "psf__requests-2317"})
CA_PATH = "requests/cacert.pem"
RESOURCE_POLICY = "immutable-image-ca-plus-admitted-core/1"


@dataclass(frozen=True, slots=True)
class RequestsCAIdentity(_Canonical):
    task_id: str
    original_ca_sha256: str
    fixture_ca_sha256: str
    result_ca_sha256: str
    wrong_ca_pem: str

    def __post_init__(self):
        if type(self.task_id) is not str or self.task_id not in FUNCTION36_TASKS | {"psf__requests-2674"}:
            raise ValueError("exact function36 or Requests2674 task required for public CA resources")
        for name in ("original_ca_sha256", "fixture_ca_sha256", "result_ca_sha256"):
            _digest(name, getattr(self, name))
        if type(self.wrong_ca_pem) is not str:
            raise ValueError("bounded public wrong CA PEM required")
        try:
            raw = self.wrong_ca_pem.encode("ascii")
        except UnicodeError:
            raise ValueError("public wrong CA must be ASCII PEM") from None
        if (not 1 <= len(raw) <= 16384 or not raw.startswith(b"-----BEGIN CERTIFICATE-----\n")
                or not raw.rstrip().endswith(b"-----END CERTIFICATE-----")
                or b"PRIVATE KEY" in raw or b"\0" in raw):
            raise ValueError("only bounded public wrong CA certificate data admitted")
        if hashlib.sha256(raw).hexdigest() == self.fixture_ca_sha256:
            raise ValueError("unrelated wrong CA must differ from the fixture CA")

    @property
    def wrong_ca_sha256(self):
        return hashlib.sha256(self.wrong_ca_pem.encode("ascii")).hexdigest()

    def as_dict(self):
        return {"schema_version": 1, "kind": "host_requests_public_ca_identity",
                **{name: getattr(self, name) for name in self.__dataclass_fields__},
                "wrong_ca_sha256": self.wrong_ca_sha256, "resource_policy": RESOURCE_POLICY,
                "candidate_ca_policy": "must_be_absent", "candidate_path": CA_PATH,
                "original_image_path": "/testbed/" + CA_PATH,
                "fixture_image_path": "/opt/p07-function-fixture/cacert.pem"}

    def validate_bytes(self, original: bytes, fixture: bytes):
        if (type(original) is not bytes or not 1 <= len(original) <= 1_000_000
                or type(fixture) is not bytes or not 1 <= len(fixture) <= 16384
                or not fixture.startswith(b"-----BEGIN CERTIFICATE-----")
                or b"PRIVATE KEY" in original or b"PRIVATE KEY" in fixture):
            raise ValueError("bounded original bundle and public fixture CA bytes required")
        result = original + b"\n" + fixture
        if len(result) > 1_000_000 or any(hashlib.sha256(raw).hexdigest() != expected for raw, expected in (
                (original, self.original_ca_sha256), (fixture, self.fixture_ca_sha256), (result, self.result_ca_sha256))):
            raise ValueError("public CA resources differ from their exact admitted bytes")
        return self.digest
