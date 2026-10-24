"""W1 API admission regressions: strict bounded input, body-size limits, and single-worker ownership.

Audit Q01: /v1/chat/completions and /v1/answer must reject unbounded or malformed payloads with a
sanitized 4xx before any backend construction, never leak submitted text in error bodies, and the
service must remain a single-process owner of its project root.
"""

import asyncio

import pytest

import atomic_flow.api.server as server

SENTINEL = "SENTINEL-4f2a91"
API_PATHS = "/v1/chat/completions"


@pytest.fixture(autouse=True)
def _needs_fastapi():
    pytest.importorskip("fastapi")


@pytest.fixture
def api_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ATOMIC_FLOW_SERVER_API_KEY", "")
    monkeypatch.setenv("ATOMIC_FLOW_REQUIRE_API_KEY", "false")
    monkeypatch.setenv("ATOMIC_FLOW_PROJECT_ROOT", str(tmp_path))
    for variable in ("WEB_CONCURRENCY", "UVICORN_WORKERS"):
        monkeypatch.delenv(variable, raising=False)
    return tmp_path


def make_client():
    from fastapi.testclient import TestClient

    return TestClient(server.create_app(), raise_server_exceptions=False)


def run_middleware(headers, chunks):
    """Drive BoundedBodyMiddleware directly with a fake ASGI transport. Returns (status, body, inner_calls, delivered)."""
    inner_calls, delivered, events = [], [], []

    class Inner:
        async def __call__(self, scope, receive, send):
            inner_calls.append(True)
            delivered.append(await receive())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

    queue = [{"type": "http.request", "body": chunk, "more_body": True} for chunk in chunks]
    queue.append({"type": "http.request", "body": b"", "more_body": False})

    async def receive():
        return queue.pop(0)

    async def send(message):
        events.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/v1/answer", "raw_path": b"/v1/answer", "query_string": b"", "root_path": "", "headers": headers,
             "client": ("testclient", 50000), "server": ("testserver", 80)}

    async def scenario():
        await server.BoundedBodyMiddleware(Inner())(scope, receive, send)

    asyncio.run(scenario())
    status = next(event["status"] for event in events if event["type"] == "http.response.start")
    body = b"".join(event.get("body", b"") for event in events if event["type"] == "http.response.body")
    return status, body, inner_calls, delivered


def test_oversized_and_malformed_chat_input_returns_4xx_never_500(api_env):
    client = make_client()
    oversized = SENTINEL + "x" * (50_001 - len(SENTINEL))
    cases = [
        {"messages": [{"role": "user", "content": oversized}]},                                   # single message over 50k chars
        {"messages": [{"role": "user", "content": [{"type": "text", "text": 123}]}]},            # numeric content part
        {"messages": [{"role": "user", "content": "hello", "weather": SENTINEL}]},               # unknown message field
        {"messages": [{"role": "wizard", "content": "hello"}]},                                   # unknown role
        {"messages": [{"role": "user", "content": [{"type": "text", "text": "a"}] * 101}]},      # 101 content parts
        {"messages": [{"role": "user", "content": "\u3002" * 45_000},                             # two messages, 270 000 bytes
                      {"role": "user", "content": "\u3002" * 45_000}]},
    ]
    for payload in cases:
        response = client.post(API_PATHS, json=payload)
        assert 400 <= response.status_code < 500, (response.status_code, response.text[:200])
        assert SENTINEL not in response.text, "rejected payload was echoed back to the client"


def test_body_size_limits(api_env):
    client = make_client()
    response = client.post("/v1/answer", content=b"x" * (server.MAX_BODY_BYTES + 1),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "body_too_large"

    # Oversize delivered as many small chunks (no Content-Length header) is still rejected before the app.
    status, body, inner_calls, _ = run_middleware([(b"content-type", b"application/json")], [b"x" * 65_536] * 17)
    assert status == 413 and b"body_too_large" in body and inner_calls == []

    # A small chunked body is buffered and re-delivered whole to the inner app.
    status, body, inner_calls, delivered = run_middleware([(b"content-type", b"application/json")],
                                                          [b'{"que', b'ry":"x"}'])
    assert status == 200 and inner_calls == [True]
    assert delivered == [{"type": "http.request", "body": b'{"query":"x"}', "more_body": False}]

    for bad_length in (b"banana", b"-1"):
        status, body, inner_calls, _ = run_middleware([(b"content-length", bad_length)], [b"{}"])
        assert status == 400 and b"invalid_content_length" in body and inner_calls == []


def test_answer_request_limits(api_env, monkeypatch):
    client = make_client()
    assert client.post("/v1/answer", json={"query": "x" * 50_001}).status_code == 422
    aggregate = {"query": "a" * 40_001, "retrieval_query": "b" * 40_001,
                 "role_prompts": {f"role{i}": "c" * 40_001 for i in range(3)}}
    assert client.post("/v1/answer", json=aggregate).status_code == 422
    assert client.post("/v1/answer", json={"query": "q",
                                           "role_prompts": {f"r{i:02d}": "v" for i in range(17)}}).status_code == 422

    # Boundary: exactly 50 000 chars passes schema validation and reaches a later stage (config/backend), never 422.
    from atomic_flow.errors import BackendError

    def refuse_backend(*args, **kwargs):
        raise BackendError("no backend is configured in tests")

    monkeypatch.setattr(server, "make_backend", refuse_backend)
    boundary = client.post("/v1/answer", json={"query": "x" * 50_000, "project": str(api_env)})
    assert boundary.status_code != 422, boundary.text[:200]


def test_composed_query_limit(api_env):
    client = make_client()
    payload = {"messages": [{"role": "user", "content": SENTINEL + "m" * 8_990}] + [
        {"role": "user", "content": "m" * 9_000} for _ in range(7)]}
    response = client.post(API_PATHS, json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "composed_query_too_large"
    assert SENTINEL not in response.text


def test_admission_rejects_before_any_model_call(api_env, monkeypatch):
    calls = []

    def forbidden_backend(*args, **kwargs):
        calls.append(args)
        raise AssertionError("admission failures must never construct a backend")

    monkeypatch.setattr(server, "make_backend", forbidden_backend)
    client = make_client()
    chat_cases = [
        {"messages": [{"role": "user", "content": "x" * 50_001}]},
        {"messages": [{"role": "user", "content": [{"type": "text", "text": 123}]}]},
        {"messages": [{"role": "user", "content": "x", "extra_field": 1}]},
        {"messages": [{"role": "wizard", "content": "x"}]},
        {"messages": [{"role": "user", "content": [{"type": "text", "text": "a"}] * 101}]},
        {"messages": [{"role": "user", "content": "\u3002" * 45_000}, {"role": "user", "content": "\u3002" * 45_000}]},
        {"messages": [{"role": "user", "content": "m" * 9_000} for _ in range(8)]},   # schema-valid, composed > 50k
        {"messages": [{"role": "assistant", "content": "no user turn"}]},             # rejected before any run
    ]
    for payload in chat_cases:
        response = client.post(API_PATHS, json=payload)
        assert 400 <= response.status_code < 500, (response.status_code, response.text[:200])
    answer_cases = [
        {"query": "x" * 50_001},
        {"query": "a" * 40_001, "retrieval_query": "b" * 40_001,
         "role_prompts": {f"role{i}": "c" * 40_001 for i in range(3)}},
        {"query": "q", "role_prompts": {f"r{i:02d}": "v" for i in range(17)}},
    ]
    for payload in answer_cases:
        assert 400 <= client.post("/v1/answer", json=payload).status_code < 500
    assert calls == []


def test_single_worker_guard_and_ready(api_env, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    with pytest.raises(RuntimeError, match="exactly one worker"):
        server.create_app()

    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    monkeypatch.setenv("UVICORN_WORKERS", "1")
    app = server.create_app()
    response = TestClient(app, raise_server_exceptions=False).get("/ready")
    assert response.status_code == 503
    assert response.json()["ready"] is False
    assert response.json()["ownership"] == "single_process"

    with TestClient(app) as live:
        assert live.get("/ready").status_code == 200
        assert live.get("/ready").json() == {"ready": True, "ownership": "single_process", "durable_runs": False}
        with pytest.raises(ValueError, match="already owns"):
            with TestClient(server.create_app()):
                pass

    # Shutdown released the lock: a fresh worker may take ownership of the same project root.
    with TestClient(server.create_app()) as relayed:
        assert relayed.get("/ready").json()["ready"] is True


def test_strict_types(api_env):
    client = make_client()
    valid_messages = [{"role": "user", "content": "x"}]
    assert client.post(API_PATHS, json={"messages": [{"role": "user", "content": 123}]}).status_code == 422
    assert client.post(API_PATHS, json={"model": 123, "messages": valid_messages}).status_code == 422
    assert client.post(API_PATHS, json={"stream": "yes", "messages": valid_messages}).status_code == 422
    assert client.post("/v1/answer", json={"query": 123}).status_code == 422
