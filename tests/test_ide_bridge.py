import http.client
import json
import threading

import pytest

from atomic_flow.integrations.ide import create_ide_bridge

TOKEN = "test-local-credential-" + "x" * 32
ORIGIN = "http://127.0.0.1:8765"


@pytest.fixture
def bridge(tmp_path):
    (tmp_path / "sample.py").write_text("answer = 42\n")
    (tmp_path / ".env").write_text("SECRET=hidden")
    server = create_ide_bridge(tmp_path, token=TOKEN, allowed_origins=[ORIGIN])
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def request(server, path="/v1/status", value=None, changes=None):
    headers = {"Authorization": "Bearer " + TOKEN, "Origin": ORIGIN, "X-Atomic-Project": server.project_id,
               "X-Atomic-Nonce": server.nonce, "Content-Type": "application/json"}
    for key, val in (changes or {}).items():
        if val is None:
            headers.pop(key, None)
        else:
            headers[key] = val
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    try:
        connection.request("POST", path, body=json.dumps(value or {}), headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_bridge_project_bound_readonly_source(bridge):
    status, result = request(bridge)
    assert status == 200 and result["paths"] == ["sample.py"] and not result["writes_enabled"]
    status, result = request(bridge, "/v1/read", {"path": "sample.py", "limit": 4})
    assert status == 200 and result["content"] == "answ" and result["next_offset"] == 4
    assert TOKEN not in json.dumps(result) and "nonce" not in result
    assert request(bridge, "/v1/edit", {"path": "sample.py", "content": "bad"})[0] == 404


@pytest.mark.parametrize("headers,status", [({"Authorization": "Bearer wrong"}, 401), ({"Origin": None}, 403),
                                           ({"Origin": "http://evil.example"}, 403), ({"X-Atomic-Project": "other"}, 409),
                                           ({"X-Atomic-Nonce": "old-instance"}, 409),
                                           ({"Authorization": "Bearer é"}, 401), ({"X-Atomic-Nonce": "é"}, 409)])
def test_all_authority_gates_before_source_access(bridge, headers, status):
    assert request(bridge, "/v1/read", {"path": "../.env"}, changes=headers)[0] == status


def test_bridge_rejects_paths_and_large_body(bridge):
    for path in ("../.env", ".env", "credentials.py", "C:/Windows/system.ini"):
        assert request(bridge, "/v1/read", {"path": path})[0] == 400
    assert request(bridge, "/v1/read", {"path": "x" * 20000})[0] == 413


def test_no_remote_bind_weak_token_or_implicit_browser_origin(tmp_path):
    with pytest.raises(ValueError, match="loopback"):
        create_ide_bridge(tmp_path, token=TOKEN, allowed_origins=[ORIGIN], host="0.0.0.0")
    with pytest.raises(ValueError, match="strong"):
        create_ide_bridge(tmp_path, token="short", allowed_origins=[ORIGIN])
    with pytest.raises(ValueError, match="Origin"):
        create_ide_bridge(tmp_path, token=TOKEN)


def test_response_cap_is_enforced_before_writing(bridge, monkeypatch):
    monkeypatch.setattr("atomic_flow.integrations.ide.MAX_RESPONSE_BYTES", 64)
    status, result = request(bridge)
    assert status == 413 and "response limit" in result["error"]
