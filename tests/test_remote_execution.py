import base64
import copy
import json
import os
import socket
import subprocess
import threading
import time

import pytest

from atomic_flow.config import user_dir
from atomic_flow.credentials import CredentialBroker, CredentialDenied
from atomic_flow.remote import (
    HTTPWorkerTransport,
    RemoteExecutor,
    RemoteOutcomeUnknown,
    RemoteWorker,
    SourceBundle,
    build_bundle,
    canonical,
    sha256,
    worker_server,
)
from atomic_flow.sdk import connect_worker

IMAGE = "sha256:" + "a" * 64
TOKEN = "test-worker-token-" + "x" * 40


def bundle_for(tmp_path, **kwargs):
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    (source / "probe.py").write_bytes(b"print('remote-ok')\n")
    return build_bundle(source, ["probe.py"], ["probe.py"], image=IMAGE, **kwargs)


class FakeJobs:
    records = {}

    def __init__(self, project, root=None, **kwargs):
        self.project = project

    def start(self, args, **kwargs):
        assert kwargs["config"].mode == "docker" and kwargs["config"].image == IMAGE
        hashes = {p.relative_to(self.project).as_posix(): sha256(p.read_bytes()) for p in self.project.rglob("*") if p.is_file()}
        ident = "job_" + sha256(str(self.project).encode())[:24]
        result = {"id": ident, "source_hashes": hashes, "status": "succeeded", "output_complete": True, "effects_unknown": False}
        self.records[ident] = result
        return result.copy()

    def status(self, ident):
        return self.records[ident].copy()

    def cancel(self, ident):
        self.records[ident]["cancel_requested"] = True
        return self.status(ident)

    def reconcile(self, ident):
        return [self.status(ident)]

    def output(self, ident, **kwargs):
        return {"text": "remote-ok\n", "data_base64": base64.b64encode(b"remote-ok\n").decode(), "complete": True}


@pytest.fixture
def fake_worker(tmp_path, monkeypatch):
    monkeypatch.setattr("atomic_flow.remote._verify_host", lambda image: None)
    monkeypatch.setattr("atomic_flow.jobs.JobStore", FakeJobs)
    FakeJobs.records = {}
    return RemoteWorker(tmp_path / "worker", image=IMAGE)


def test_bundle_is_explicit_source_only_and_hash_checked(tmp_path):
    bundle = bundle_for(tmp_path)
    assert bundle.checked_files() == {"probe.py": b"print('remote-ok')\n"}
    (tmp_path / "source" / ".env.enc").write_text("do-not-archive")
    (tmp_path / "source" / "credentials.json").write_text("do-not-archive")
    for path in [".env.enc", "credentials.json", "../outside.py", "probe.py:secret", "/probe.py"]:
        with pytest.raises((ValueError, OSError)):
            build_bundle(tmp_path / "source", [path], ["probe.py"], image=IMAGE)
    assert "do-not-archive" not in json.dumps(bundle.model_dump())
    raw = bundle.model_dump()
    raw["files"][0]["content"] = base64.b64encode(b"substituted").decode()
    with pytest.raises(ValueError, match="hash mismatch"):
        SourceBundle.model_validate(raw).checked_files()


def test_bundle_rejects_duplicate_paths_unsupported_schema_and_mutable_image(tmp_path):
    bundle = bundle_for(tmp_path).model_dump()
    for patch in [{"version": 2}, {"image": "ubuntu:latest"}, {"profile": "unrestricted"},
                  {"files": bundle["files"] * 2}, {"args": ["\0"]}]:
        with pytest.raises(ValueError):
            SourceBundle.model_validate({**bundle, **patch}).checked_files()


def test_request_is_deduplicated_without_reexecution_and_observations_bound(tmp_path, fake_worker):
    bundle = bundle_for(tmp_path)
    client = RemoteExecutor(fake_worker)
    receipt = client.submit(bundle)
    assert client.submit(bundle) == receipt
    assert client.reconcile(bundle) == receipt
    assert len(FakeJobs.records) == 1
    assert client.status(receipt)["status"]["output_complete"]
    assert client.output(receipt)["output"]["text"] == "remote-ok\n"
    assert client.cancel(receipt)["status"]["cancel_requested"]
    damaged = dict(receipt, source_hash="0" * 64)
    with pytest.raises(ValueError, match="receipt mismatch"):
        client.status(damaged)


def test_interrupted_submission_is_never_automatically_replayed(tmp_path, fake_worker, monkeypatch):
    bundle = bundle_for(tmp_path)
    calls = []
    def crash(*args, **kwargs):
        calls.append(1)
        raise OSError("lost after durable intent")
    monkeypatch.setattr(FakeJobs, "start", crash)
    client = RemoteExecutor(fake_worker)
    with pytest.raises(OSError):
        client.submit(bundle)
    with pytest.raises(ValueError, match="outcome unknown"):
        client.submit(bundle)
    assert len(calls) == 1


def test_worker_refuses_hash_mismatch_and_request_id_substitution(tmp_path, fake_worker):
    bundle = bundle_for(tmp_path)
    with pytest.raises(ValueError, match="bundle or pinned image mismatch"):
        fake_worker.call("submit", {"bundle": bundle.model_dump(), "bundle_hash": "0" * 64})
    receipt = RemoteExecutor(fake_worker).submit(bundle)
    other = bundle.model_copy(update={"args": ["-c", "print('changed')"]})
    with pytest.raises(ValueError, match="different bundle"):
        RemoteExecutor(fake_worker).submit(other)
    assert RemoteExecutor(fake_worker).status(receipt)["status"]["status"] == "succeeded"


def test_client_detects_substituted_receipt_result_and_source_manifest(tmp_path, fake_worker):
    client = RemoteExecutor(fake_worker)
    receipt = client.submit(bundle_for(tmp_path))
    class Tamper:
        mode = "result"
        def call(self, action, payload):
            value = copy.deepcopy(fake_worker.call(action, payload))
            if self.mode == "receipt":
                value["receipt"]["image"] = "sha256:" + "0" * 64
            elif self.mode == "source":
                value["status"]["source_hashes"] = {}
                value["result_hash"] = sha256(canonical(value["status"]))
            elif action == "output":
                value["output"]["text"] = "substitution"
            else:
                value["status"]["status"] = "failed"
            return value
    transport = Tamper()
    client = RemoteExecutor(transport)
    for mode in ("result", "receipt", "source"):
        transport.mode = mode
        with pytest.raises(ValueError):
            client.status(receipt)
    transport.mode = "result"
    with pytest.raises(ValueError, match="output manifest mismatch"):
        client.output(receipt)


def test_lost_or_malformed_submit_ack_is_uncertain_and_lookup_does_not_execute(tmp_path, fake_worker):
    bundle = bundle_for(tmp_path)
    class LostAck:
        def call(self, action, payload):
            result = fake_worker.call(action, payload)
            if action == "submit":
                return {"version": 999}
            return result
    client = RemoteExecutor(LostAck())
    with pytest.raises(RemoteOutcomeUnknown, match="do not replay"):
        client.submit(bundle)
    receipt = client.reconcile(bundle)
    assert client.status(receipt)["status"]["status"] == "succeeded"
    assert len(FakeJobs.records) == 1


def test_scoped_transport_preserves_unknown_submit_outcome(tmp_path, monkeypatch):
    from atomic_flow.remote import ScopedWorkerTransport
    broker = CredentialBroker(resolver=lambda env: TOKEN)
    grant = broker.grant(project=tmp_path, connector="remote-worker", operations=["submit"], env_name="TEST_TOKEN", approved=True)
    def lost(*args, **kwargs):
        raise RemoteOutcomeUnknown("untrusted provider text containing " + TOKEN)
    monkeypatch.setattr(HTTPWorkerTransport, "call", lost)
    transport = ScopedWorkerTransport(tmp_path, "https://worker.example", broker, grant.id)
    with pytest.raises(RemoteOutcomeUnknown) as exc:
        transport.call("submit", {})
    assert TOKEN not in str(exc.value)


@pytest.mark.parametrize("url", ["http://example.com", "http://127.0.0.1", "https://u:p@example.com", "https://example.com/?x=y"])
def test_transport_requires_tls_no_embedded_credentials_or_query(url):
    with pytest.raises(ValueError):
        HTTPWorkerTransport(url, TOKEN)
    assert TOKEN not in repr(HTTPWorkerTransport("https://example.com", TOKEN))


def test_real_http_auth_scoped_credentials_revocation_and_bounded_envelopes(tmp_path, fake_worker):
    server = worker_server(fake_worker, TOKEN)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:" + str(server.server_port)
    try:
        broker = CredentialBroker(resolver=lambda env: TOKEN)
        grant = broker.grant(project=tmp_path, connector="remote-worker", operations=["submit", "status", "output", "cancel"],
                             env_name="TEST_WORKER_TOKEN", approved=True)
        client = connect_worker(tmp_path, url, broker=broker, grant_id=grant.id, allow_loopback_http=True)
        receipt = client.submit(bundle_for(tmp_path))
        assert client.output(receipt)["output"]["text"] == "remote-ok\n"
        assert TOKEN not in json.dumps(client.status(receipt))
        broker.revoke(grant.id)
        with pytest.raises(CredentialDenied):
            client.cancel(receipt)
        with pytest.raises(ValueError, match="401"):
            HTTPWorkerTransport(url, "wrong" * 10, True).call("status", {"receipt": receipt})
        with pytest.raises(RemoteOutcomeUnknown):
            HTTPWorkerTransport(url, TOKEN, True).call("submit", {"not-a-bundle": True})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_worker_is_linux_and_image_pinned_even_before_serving(tmp_path):
    with pytest.raises((ValueError, OSError)):
        RemoteWorker(tmp_path / "worker", image="mutable:tag")
    with pytest.raises(ValueError):
        worker_server(object(), TOKEN, host="0.0.0.0")


def test_actual_job_snapshot_keeps_private_worker_source_and_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr("atomic_flow.remote._verify_host", lambda image: None)
    monkeypatch.setattr("atomic_flow.jobs.subprocess.Popen", lambda *args, **kwargs: None)
    worker = RemoteWorker(user_dir() / "remote-worker", image=IMAGE)
    client = RemoteExecutor(worker)
    bundle = bundle_for(tmp_path)
    receipt = client.submit(bundle)
    status = client.status(receipt)["status"]
    assert status["status"] == "queued"
    assert status["source_hashes"] == {item.path: item.sha256 for item in bundle.files}
    assert not status["output_complete"]
    private = worker._store(receipt)._directory(receipt["job_id"]) / "workspace" / "probe.py"
    assert private.read_bytes() == b"print('remote-ok')\n"


def test_worker_bounds_connections_before_reading_unauthenticated_headers(fake_worker):
    server = worker_server(fake_worker, TOKEN)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connections = []
    try:
        for _ in range(4):
            connection = socket.create_connection(server.server_address, timeout=5)
            connection.sendall(b"POST /v1/status HTTP/1.1\r\n")  # incomplete headers
            connections.append(connection)
        with socket.create_connection(server.server_address, timeout=5) as extra:
            extra.sendall(b"POST /v1/status HTTP/1.1\r\n")
            assert b"429" in extra.recv(256)
    finally:
        for connection in connections:
            connection.close()
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.skipif(os.getenv("ATOMIC_FLOW_TEST_DOCKER") != "1", reason="requires Docker-capable Linux host")
def test_real_remote_protocol_docker_job_and_cancel(tmp_path, monkeypatch):
    image = subprocess.check_output(["docker", "image", "inspect", "atomic-flow-checks:local", "--format", "{{.Id}}"], text=True).strip()
    project = tmp_path / "source"
    project.mkdir()
    (project / "probe.py").write_text("import os\nassert 'REMOTE_SECRET_PROBE' not in os.environ\nprint('remote-container-ok')\n")
    monkeypatch.setenv("REMOTE_SECRET_PROBE", "host-only-never-forward")
    worker = RemoteWorker(tmp_path / "worker", image=image)
    server = worker_server(worker, TOKEN)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = RemoteExecutor(HTTPWorkerTransport(f"http://127.0.0.1:{server.server_port}", TOKEN, True))
    try:
        receipt = client.submit(build_bundle(project, ["probe.py"], ["probe.py"], image=image))
        def wait(receipt, terminal):
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                result = client.status(receipt)["status"]
                if result["status"] in terminal:
                    return result
                time.sleep(0.1)
            raise AssertionError("remote job did not reach expected state")
        result = wait(receipt, {"succeeded", "failed", "interrupted"})
        assert result["status"] == "succeeded", result
        assert result["output_complete"] and not result["effects_unknown"]
        assert "remote-container-ok" in client.output(receipt)["output"]["text"]
        slow = build_bundle(project, ["probe.py"], ["-c", "import time; time.sleep(60)"], image=image)
        pending = client.submit(slow)
        wait(pending, {"running"})
        client.cancel(pending)
        cancelled = wait(pending, {"cancelled", "failed", "interrupted"})
        assert cancelled["status"] == "cancelled", cancelled
        assert TOKEN not in json.dumps(cancelled) and "host-only-never-forward" not in json.dumps(cancelled)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
