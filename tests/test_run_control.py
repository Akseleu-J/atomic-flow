"""Fault-injected transport/control tests. No external APIs or paid model calls."""
from __future__ import annotations

import asyncio
import io
import json
import threading
import time
import urllib.error
import uuid
from email.message import Message

import pytest
from conftest import ScriptedBackend
from pydantic import BaseModel, ValidationError

from atomic_flow.agents.base import Tool, ToolContract, ToolRegistry
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.agents.loop import execute_tool
from atomic_flow.agents.policy import PolicyEngine, Rule
from atomic_flow.agents.task import Task, load, save
from atomic_flow.agents.task_budget import TaskBudgetBackend, check_accounting
from atomic_flow.backends import http
from atomic_flow.backends.base import ChatResult
from atomic_flow.backends.cancellation import cancellation_scope
from atomic_flow.backends.openai_compatible import OpenAICompatibleBackend
from atomic_flow.budget import SpendLedger
from atomic_flow.config import AtomicConfig, BackendConfig, user_dir
from atomic_flow.errors import BudgetExceeded, ResponseFormatError
from atomic_flow.llm.orchestrator import _Run
from atomic_flow.llm.prompts import PromptRegistry
from atomic_flow.runtime import ControlMessage, EventBuffer, RunControl, RunEvent
from atomic_flow.sdk import start_answer
from atomic_flow.sessions import SessionJournal


def payload():
    return {"choices": [{"message": {"content": '{"text":"ok"}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost_usd": 0.002}}


class Response(io.BytesIO):
    def __init__(self, value=None):
        super().__init__(json.dumps(value or payload()).encode())


def backend():
    return OpenAICompatibleBackend(BackendConfig(model="mock", base_url="http://mock/v1", max_retries=3, timeout_seconds=5,
                                                max_output_tokens=256, price_in_per_mtok=1, price_out_per_mtok=1))


def test_cancel_before_send_never_opens_transport(monkeypatch):
    control = RunControl()
    control.submit(ControlMessage(action="cancel"))
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("not sent"))
    with cancellation_scope(control.check), pytest.raises(http.TransportCancelled) as error:
        backend().chat([])
    assert error.value.request_outcome == "not_sent"


@pytest.mark.parametrize("phase", ["dns", "connect", "read", "error_body"])
def test_cancel_during_transport_retains_unknown_charge_and_blocks_replay(tmp_path, monkeypatch, phase):
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def stall():
        entered.set()
        assert release.wait(4)

    class SlowResponse(Response):
        def read(self, *args):
            stall()
            return super().read(*args)

        def close(self):
            super().close()
            exited.set()

    def opener(*args, **kw):
        calls.append(True)
        if phase in {"dns", "connect"}:
            stall()  # faults at the resolver/connect boundary, no real network
        if phase == "error_body":
            raise urllib.error.HTTPError("http://mock", 500, "error", {}, SlowResponse())
        return SlowResponse() if phase == "read" else Response()

    monkeypatch.setattr(http.urllib.request, "urlopen", opener)
    task = Task("task_" + uuid.uuid4().hex[:12], "report", str(tmp_path), [])
    save(task)
    control = RunControl()
    wrapped = TaskBudgetBackend(backend(), task, AtomicConfig(), control.check)
    errors = []

    def call():
        try:
            wrapped.chat([])
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=call)
    thread.start()
    try:
        assert entered.wait(3)
        control.submit(ControlMessage(action="cancel"))
        thread.join(1)
        assert not thread.is_alive() and isinstance(errors[0], TaskCancelled)
        assert load(task.id).budget_events[0]["status"] == "unknown"
        assert SpendLedger(10).today_total() > 0
        with pytest.raises(BudgetExceeded, match="unresolved"):
            check_accounting(load(task.id))
        assert len(calls) == 1
    finally:
        release.set()
        thread.join(4)
        # Wait for the abandoned transport slot to be returned before next case.
        deadline = time.monotonic() + 2
        while http._SLOTS._value != 4 and time.monotonic() < deadline:
            time.sleep(0.01)


@pytest.mark.parametrize("failure", ["lost", "timeout", "json", "500"])
def test_uncertain_outcomes_are_never_automatically_retried(monkeypatch, failure):
    calls = []

    def opener(*args, **kw):
        calls.append(True)
        if failure == "lost":
            raise urllib.error.URLError("lost")
        if failure == "timeout":
            raise TimeoutError("lost")
        if failure == "500":
            raise urllib.error.HTTPError("http://mock", 500, "error", {}, io.BytesIO(b"failed"))
        return io.BytesIO(b"not JSON")

    monkeypatch.setattr(http.urllib.request, "urlopen", opener)
    with pytest.raises(http.TransportError) as error:
        backend().chat([])
    assert len(calls) == 1 and error.value.request_outcome == "unknown"


def test_explicit_refusal_can_retry_and_redacts_secret(monkeypatch):
    calls = []
    headers = Message()
    headers["Retry-After"] = "0.1"

    def opener(*a, **kw):
        calls.append(True)
        if len(calls) == 1:
            raise urllib.error.HTTPError("http://mock", 429, "throttled", headers, io.BytesIO(b"secret-value"))
        return Response()

    monkeypatch.setattr(http.urllib.request, "urlopen", opener)
    assert http.post_json("http://mock", {}, {}, timeout=1, retries=1, secrets=("secret-value",)) == payload()
    assert len(calls) == 2
    with pytest.raises(http.HTTPStatusError) as error:
        calls.clear()
        http.post_json("http://mock", {}, {}, timeout=1, retries=0, secrets=("secret-value",))
    assert "secret-value" not in str(error.value) and error.value.request_outcome == "refused"


def test_cancel_backoff_releases_refused_reservation(tmp_path, monkeypatch):
    control = RunControl()
    calls = []

    def opener(*a, **kw):
        calls.append(True)
        control.submit(ControlMessage(action="cancel"))
        raise urllib.error.HTTPError("http://mock", 429, "refused", {}, io.BytesIO(b"busy"))

    monkeypatch.setattr(http.urllib.request, "urlopen", opener)
    with cancellation_scope(control.check), pytest.raises(http.TransportCancelled) as error:
        backend().chat([])
    assert error.value.request_outcome == "refused" and len(calls) == 1

    class Refused:
        name, config = "mock", backend().config

        def chat(self, *a, **kw):
            raise http.TransportCancelled("refused")

    task = Task("task_" + uuid.uuid4().hex[:12], "report", str(tmp_path), [])
    save(task)
    with pytest.raises(TaskCancelled):
        TaskBudgetBackend(Refused(), task, AtomicConfig(), lambda: None).chat([])
    assert load(task.id).budget_events[0]["status"] == "completed"
    assert SpendLedger(10).today_total() == 0
    check_accounting(load(task.id))


def test_received_response_is_accounted_before_cancel(tmp_path, monkeypatch):
    control = RunControl()

    class Charged(Response):
        def read(self, *a):
            control.submit(ControlMessage(action="cancel"))
            return super().read(*a)

    monkeypatch.setattr(http.urllib.request, "urlopen", lambda *a, **kw: Charged())
    task = Task("task_" + uuid.uuid4().hex[:12], "report", str(tmp_path), [])
    save(task)
    with pytest.raises(TaskCancelled):
        TaskBudgetBackend(backend(), task, AtomicConfig(), control.check).chat([])
    restored = load(task.id)
    assert restored.budget_spent == 0.002 and restored.budget_events[0]["status"] == "completed"
    assert SpendLedger(10).today_total() == 0.002


def test_stuck_workers_are_bounded_and_refuse_before_send(monkeypatch):
    # Saturation uses an isolated semaphore; no background test threads leak.
    monkeypatch.setattr(http, "_SLOTS", threading.BoundedSemaphore(0))
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("no worker slot"))
    with pytest.raises(http.TransportError) as error:
        backend().chat([])
    assert error.value.request_outcome == "not_sent"


def test_event_gaps_terminal_retention_limits_redaction_and_version():
    buffer = EventBuffer(capacity=2)
    for index in range(10):
        buffer.emit("model_response", {"index": index, "api_key": "private"}, call_id="model_1")
    buffer.emit("run_completed", {"result": "done"}, terminal=True)
    event = buffer.next()
    assert event.sequence == 10 and event.dropped_before == 9
    assert event.payload["api_key"] == "[REDACTED]"
    terminal = buffer.next()
    assert terminal == buffer.terminal and terminal.kind == "run_completed"
    buffer.emit("bad", {}, terminal=True)
    assert buffer.next(0) is None and buffer.terminal == terminal
    assert RunEvent.model_validate_json(terminal.model_dump_json()) == terminal
    with pytest.raises(ValidationError):
        ControlMessage(version=2, action="cancel")
    buffer = EventBuffer()
    buffer.emit("tool_result", {"body": "x" * 70000})
    assert buffer.next().payload["reason"] == "event_payload_limit"


def test_control_queue_finish_and_cancel_validation():
    control = RunControl()
    with pytest.raises(ValueError):
        control.submit(ControlMessage(action="cancel", text="extra"))
    with pytest.raises(ValueError):
        control.submit(ControlMessage(action="steer"))
    for _ in range(16):
        control.submit(ControlMessage(action="steer", text="guidance"))
    with pytest.raises(ValueError, match="full"):
        control.submit(ControlMessage(action="steer", text="guidance"))
    with pytest.raises(ValueError, match="unapplied"):
        control.finish()
    assert len(control.drain()) == 16
    control.finish()
    with pytest.raises(ValueError, match="finished"):
        control.submit(ControlMessage(action="cancel"))


def test_sdk_events_correlate_with_durable_session_and_result(project, config):
    model = ScriptedBackend({"answerer": {"text": "answer", "confidence": "low"}})
    handle = start_answer(config, project, model, "refund", mode="simple", capacity=2)
    result = handle.result(5)
    events = list(handle.events())
    assert events[-1].kind == "run_completed" and events[-1].run_id == result.ledger_id == handle.run_id
    snapshot = SessionJournal.open(handle.run_id).read()
    request = next(event for event in snapshot.events if event["event_type"] == "model_request")
    response = next(event for event in snapshot.events if event["event_type"] == "model_response")
    assert request["call_id"] == response["call_id"] and not snapshot.pending_call_ids
    assert events[-1].payload["result"]["text"] == "answer"
    assert SpendLedger(10).today_total() == 0.001  # no aggregate double charge
    with pytest.raises(ValueError):
        handle.steer("too late")


def test_sdk_cancel_unknown_keeps_ask_reservation(project, config, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def opener(*a, **kw):
        entered.set()
        assert release.wait(4)
        return Response()

    monkeypatch.setattr(http.urllib.request, "urlopen", opener)
    handle = start_answer(config, project, backend(), "refund", mode="simple")
    try:
        assert entered.wait(3)
        handle.cancel()
        with pytest.raises(TaskCancelled):
            handle.result(2)
        assert list(handle.events())[-1].kind == "run_cancelled"
        rows = [json.loads(line) for line in SpendLedger(10).path.read_text().splitlines()]
        assert rows[-1]["status"] == "unknown" and SpendLedger(10).today_total() > 0
        assert len({row["call_id"] for row in rows}) == 1
    finally:
        release.set()


def test_steering_supersedes_response_and_propagates_to_next_role(project, config):
    entered, release = threading.Event(), threading.Event()

    def old(user):
        entered.set()
        assert release.wait(4)
        return {"text": "stale", "confidence": "low"}

    model = ScriptedBackend({"parser": {}, "answerer": [old, {"text": "new", "confidence": "low"}],
                             "critic": {"verdict": "ok", "citation_coverage": 1}})
    handle = start_answer(config, project, model, "refund", mode="medium")
    assert entered.wait(3)
    handle.steer("Please answer briefly")
    release.set()
    assert handle.result(5).text == "new"
    assert model.roles() == ["parser", "answerer", "answerer", "critic"]
    assert "Please answer briefly" in json.dumps(model.calls[-1][1])
    kinds = [event.kind for event in handle.events()]
    assert "response_discarded" in kinds and "steering_applied" in kinds
    assert SpendLedger(10).today_total() == pytest.approx(0.004)


class WriteArgs(BaseModel):
    value: str


class Write(Tool):
    name, description, args_schema = "write_test", "Write test", WriteArgs
    contract = ToolContract(effects=("write",), idempotent=False)

    def run(self, args, ctx):
        (ctx.project_root / "effect.txt").write_text(args.value)
        return {"written": args.value}


def test_steering_during_approval_blocks_mutation_and_during_tool_applies_later(tmp_path):
    control, registry = RunControl(), ToolRegistry()
    registry.register(Write())

    def approve(*a):
        control.submit(ControlMessage(action="steer", text="stop this edit"))
        return True

    ctx = ToolContext(tmp_path, {}, edits_enabled=True, policy=PolicyEngine([Rule("write_test", "ask")]),
                      approval_callback=approve, action_boundary=control.boundary)
    call = {"tool": "write_test", "args": {"value": "original args"}}
    result, _ = execute_tool(call, registry, ctx, allowed_tools=["write_test"])
    assert result["outcome"] == "denied" and not (tmp_path / "effect.txt").exists()
    assert control.drain() == ["stop this edit"]

    class SteeringWrite(Write):
        def run(self, args, ctx):
            control.submit(ControlMessage(action="steer", text="next action"))
            return super().run(args, ctx)

    registry = ToolRegistry()
    registry.register(SteeringWrite())
    ctx.policy = PolicyEngine([Rule("write_test", "auto")])
    result, _ = execute_tool(call, registry, ctx, allowed_tools=["write_test"])
    assert result["outcome"] == "success" and (tmp_path / "effect.txt").read_text() == "original args"
    assert control.boundary() and control.drain() == ["next action"]


def test_steering_preserves_history_indices_and_admission(tmp_path):
    from atomic_flow.config import ConversationConfig
    from atomic_flow.llm.history import RequestHistory
    run = _Run(backend(), PromptRegistry(tmp_path), SpendLedger(0), "query", None)
    run.control = RunControl()
    history = RequestHistory(ConversationConfig(max_request_bytes=4096, recent_tool_results=1), backend().config)
    messages = [{"role": "system", "content": "host permissions"}]
    history.append_result(messages, {"source": "x" * 2500})
    run.control.submit(ControlMessage(action="steer", text="short"))
    run.apply_steering(messages)
    history.append_result(messages, {"source": "y" * 600})
    run.control.submit(ControlMessage(action="steer", text="additional"))
    run.apply_steering(messages)
    history.prepare(messages)
    assert "history_body_omitted" in messages[1]["content"] and "y" * 600 in messages[-1]["content"]
    assert "short" in messages[2]["content"] and "additional" in messages[2]["content"]


def test_api_run_stream_auth_result_version_and_root(project, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import atomic_flow.api.server as server
    monkeypatch.setenv("ATOMIC_FLOW_SERVER_API_KEY", "k" * 32)
    monkeypatch.setenv("ATOMIC_FLOW_PROJECT_ROOT", str(project))
    monkeypatch.setattr(server, "make_backend", lambda *a, **k: ScriptedBackend({"answerer": {"text": "ok"}}))
    client = TestClient(server.create_app())
    headers = {"Authorization": "Bearer " + "k" * 32}
    assert client.post("/v1/runs", json={"query": "x"}).status_code == 401
    assert client.post("/v1/runs", headers=headers, json={"query": "x", "project": str(project.parent)}).status_code == 403
    response = client.post("/v1/runs", headers=headers, json={"query": "refund", "project": str(project), "mode": "simple"})
    assert response.status_code == 202
    urls = response.json()
    stream = client.get(urls["events_url"], headers=headers)
    assert stream.status_code == 200 and "event: run_completed" in stream.text
    event = json.loads(stream.text.split("event: run_completed\ndata: ")[1].split("\n")[0])
    assert event["version"] == 1 and event["payload"]["result"]["text"] == "ok"
    assert client.get(urls["result_url"]).status_code == 401
    assert client.get(urls["result_url"], headers=headers).json()["result"]["text"] == "ok"
    assert client.post(urls["control_url"], headers=headers, json={"version": 2, "action": "cancel"}).status_code == 422
    assert client.post(urls["control_url"], headers=headers, json={"action": "cancel"}).status_code == 409
    assert client.get(urls["events_url"], headers=headers).text.count("event: run_completed") == 1


def test_api_disconnect_cancels_wait_without_replaying(project, monkeypatch):
    pytest.importorskip("fastapi")
    import httpx

    import atomic_flow.api.server as server
    entered, release = threading.Event(), threading.Event()

    def opener(*a, **kw):
        entered.set()
        assert release.wait(5)
        return Response()

    monkeypatch.setenv("ATOMIC_FLOW_PROJECT_ROOT", str(project))
    monkeypatch.setattr(server, "make_backend", lambda *a, **k: backend())
    monkeypatch.setattr(http.urllib.request, "urlopen", opener)
    app = server.create_app()

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            response = await client.post("/v1/runs", json={"query": "refund", "project": str(project), "mode": "simple"})
            urls = response.json()
            assert await asyncio.to_thread(entered.wait, 3)
            sent = []
            first = True

            async def receive():
                nonlocal first
                if first:
                    first = False
                    return {"type": "http.request", "body": b"", "more_body": False}
                return {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)

            scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"}, "http_version": "1.1",
                     "method": "GET", "scheme": "http", "path": urls["events_url"], "raw_path": urls["events_url"].encode(),
                     "query_string": b"", "headers": [], "client": ("127.0.0.1", 1), "server": ("local", 80)}
            await app(scope, receive, send)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                result = await client.get(urls["result_url"])
                if result.status_code != 202:
                    break
                await asyncio.sleep(0.025)
            assert result.json()["error"]["code"] == "TransportCancelled"

    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_cli_events_use_same_protocol(project, monkeypatch, capsys):
    import atomic_flow.cli as cli
    monkeypatch.setattr(cli, "make_backend", lambda *a, **kw: ScriptedBackend({"answerer": {"text": "ok"}}))
    args = cli._parser().parse_args(["ask", "refund", "--project", str(project), "--mode", "simple", "--events"])
    assert cli.cmd_ask(args) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[-1]["kind"] == "run_completed" and {row["version"] for row in rows} == {1}
    assert "messages" not in json.dumps(rows[:-1])
    assert (user_dir() / "sessions" / f"session_{rows[-1]['run_id']}.jsonl").is_file()


def test_unknown_cost_blocks_format_repair_and_keeps_hold(project, config):
    class Unknown(ScriptedBackend):
        def chat(self, messages, **kwargs):
            super().chat(messages, **kwargs)
            return ChatResult("invalid JSON", cost_usd=None)

    model = Unknown({"answerer": {"text": "ignored"}})
    handle = start_answer(config, project, model, "refund", mode="simple")
    with pytest.raises(BudgetExceeded, match="unresolved model cost"):
        handle.result(5)
    assert len(model.calls) == 1 and SpendLedger(10).today_total() > 0
    rows = [json.loads(line) for line in SpendLedger(10).path.read_text().splitlines()]
    assert rows[-1]["call_id"] == handle.run_id + "_model_1" and rows[-1]["status"] == "unknown"


def test_invalid_request_is_not_sent_and_sdk_consumer_is_exclusive(project, config):
    with pytest.raises(http.TransportError) as error:
        http.post_json("bad-url", {}, {}, timeout=1, retries=3)
    assert error.value.request_outcome == "not_sent"
    with pytest.raises(http.TransportError) as error:
        http.post_json("http://mock", {"bad": float("nan")}, {}, timeout=1, retries=3)
    assert error.value.request_outcome == "not_sent"
    handle = start_answer(config, project, ScriptedBackend({"answerer": {"text": "ok"}}), "refund", mode="simple")
    handle.result(5)
    events = handle.events()
    next(events)
    with pytest.raises(ValueError, match="one event consumer"):
        next(handle.events())
    events.close()


def test_http_refused_optional_parameter_is_negotiated_once(monkeypatch):
    requests = []

    def opener(request, **kw):
        body = json.loads(request.data)
        requests.append(body)
        if "response_format" in body:
            raise urllib.error.HTTPError("http://mock", 400, "bad", {}, io.BytesIO(b"response_format unsupported"))
        return Response()

    monkeypatch.setattr(http.urllib.request, "urlopen", opener)
    assert backend().chat([], json_mode=True).cost_usd == 0.002
    assert len(requests) == 2 and "response_format" not in requests[-1]


def test_steering_during_critic_requires_new_answer(project, config):
    entered, release = threading.Event(), threading.Event()

    def old_critic(user):
        entered.set()
        assert release.wait(20)
        return {"verdict": "ok", "citation_coverage": 1}

    model = ScriptedBackend({"parser": {}, "answerer": [{"text": "old"}, {"text": "new"}],
                             "critic": [old_critic, {"verdict": "ok", "citation_coverage": 1}]})
    handle = start_answer(config, project, model, "refund", mode="medium")
    # Generous budget: the assertions verify steering semantics, not scheduler latency.
    assert entered.wait(20)
    handle.steer("Change the answer")
    release.set()
    assert handle.result(5).text == "new"
    assert model.roles().count("answerer") == 2


def test_discarded_native_call_id_cannot_execute_on_reuse(project, config):
    from atomic_flow.backends.base import NativeToolCall
    from atomic_flow.llm.orchestrator import Orchestrator
    control = RunControl()

    class Reusing:
        name = "native-mock"
        config = BackendConfig(type="openai-compatible", model="mock", tool_protocol="native",
                               max_output_tokens=256, price_in_per_mtok=1, price_out_per_mtok=1)
        calls = 0

        def chat_tools(self, messages, **kw):
            self.calls += 1
            if self.calls == 1:
                control.submit(ControlMessage(action="steer", text="reconsider"))
            return ChatResult("", cost_usd=0.001, tool_calls=[NativeToolCall("reused", "read_file", '{"path":"invoice_service.py"}')])

    config.experimental.native_tools = True
    model = Reusing()
    with pytest.raises(ResponseFormatError, match="ID was repeated"):
        Orchestrator(config, project, model, control=control).answer("refund", mode="simple", request_id="reuse")
    snapshot = SessionJournal.open("reuse").read()
    assert model.calls == 2 and not any(event["event_type"] == "tool_request" for event in snapshot.events)
