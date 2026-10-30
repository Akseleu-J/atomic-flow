"""Offline queue contract checks; no GitHub or model credentials."""
import base64
import copy
import json
import threading

import pytest

from atomic_flow.backends.base import ResponseShapeError
from atomic_flow.backends.github_queue import (
    MAX_WIRE_BYTES,
    MODEL,
    GitHubContentsQueueTransport,
    GitHubQueueBackend,
    QueueAdmissionRefused,
    QueueOutcomeUnknown,
    canonical,
    digest,
    response_packet,
    validate_request,
)
from atomic_flow.config import BackendConfig


def test_concurrent_chat_cannot_race_clear_check_and_intent(tmp_path, monkeypatch):
    actor = backend(tmp_path)
    entered, release = threading.Event(), threading.Event()
    errors = []
    original = actor._require_clear
    def blocked_clear():
        original()
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(actor, "_require_clear", blocked_clear)
    def run():
        try:
            actor.chat([{"role": "user", "content": "one"}])
        except Exception as exc:
            errors.append(exc)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(5)
        with pytest.raises(QueueAdmissionRefused, match="admission lease"):
            actor.chat([{"role": "user", "content": "two"}])
        assert actor.transport.requests == []
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and not errors and len(actor.transport.requests) == 1


def test_context_cancellation_after_enqueue_keeps_unknown_and_blocks_new_attempt(tmp_path):
    from atomic_flow.agents.execution import TaskCancelled
    from atomic_flow.backends.cancellation import cancellation_scope
    queue = Queue("timeout")
    actor = backend(tmp_path, queue)
    def cancel():
        if queue.requests:
            raise TaskCancelled("cancelled after enqueue")
    with cancellation_scope(cancel), pytest.raises(QueueOutcomeUnknown):
        actor.chat([{"role": "user", "content": "one"}])
    with pytest.raises(QueueOutcomeUnknown, match="prior model queue"):
        actor.chat([{"role": "user", "content": "two"}])
    assert len(queue.requests) == 1


def test_context_cancellation_before_enqueue_is_known_not_sent(tmp_path):
    from atomic_flow.agents.execution import TaskCancelled
    from atomic_flow.backends.cancellation import cancellation_scope
    actor = backend(tmp_path)
    def cancel():
        raise TaskCancelled("cancelled before queue admission")
    with cancellation_scope(cancel), pytest.raises(TaskCancelled) as error:
        actor.chat([{"role": "user", "content": "one"}])
    assert error.value.request_outcome == "not_sent" and actor.transport.requests == []

IDENTITY = dict(campaign_id="p07", owner="akseley", arm="atomic", task_id="sympy_21614",
                framework_sha="a" * 40, workflow_sha="b" * 40, profile_sha256="c" * 64)
BINDING = dict(repo="example/scratch", branch="codex/p07-model-queue", prefix="p07-model-queue")


def config(**changes):
    return BackendConfig(name="queue", model=MODEL, max_retries=0,
                         max_input_tokens=1000, max_output_tokens=128, **changes)


def physical(req):
    return dict(content='{"answer":"ok"}', tokens_in=10, tokens_out=8, cost_usd="0.001",
                latency_ms=1, truncated=False, cost_source="provider_reported",
                resolved_backend="kaggle_model_proxy", resolved_model=MODEL, attempt_id=req["call_id"])


class Queue:
    binding = BINDING

    def __init__(self, outcome="completed"):
        self.requests = []
        self.outcome = outcome

    def put_request(self, call_id, req):
        self.requests.append(copy.deepcopy(req))

    def get_response(self, call_id):
        req = self.requests[-1]
        if self.outcome == "timeout":
            return None
        if self.outcome == "refused":
            return response_packet(req, state="refused", error_code="admission_refused")
        if self.outcome == "unknown":
            return response_packet(req, state="unknown", error_code="physical_or_accounting_outcome_unknown")
        packet = response_packet(req, state="completed", result=physical(req),
                                 error_code="response_shape" if self.outcome == "shape" else "none")
        if self.outcome == "tampered":
            packet["identity_sha256"] = "d" * 64
        return packet


def backend(tmp_path, queue=None, **kwargs):
    return GitHubQueueBackend(config(), queue or Queue(), IDENTITY, tmp_path, **kwargs)


def test_canonical_vector_matches_javascript():
    value = {"z": 0, "2": 2, "a": ["Привет 😀 é", True, None], "10": 1}
    assert canonical(value).decode() == '{"10":1,"2":2,"a":["Привет 😀 é",true,null],"z":0}'
    assert digest(value) == "c690a66b88d2841724c748c3d2209af0e8eb8179ac52c004eae5f398e56a28b3"


@pytest.mark.parametrize("value", [1.2, float("nan"), 2**53, {"é": 1}, "\ud800"])
def test_canonical_rejects_non_wire_values(value):
    with pytest.raises((ValueError, UnicodeError)):
        canonical(value)


def test_completed_result_has_physical_usage_and_immutable_request(tmp_path):
    queue = Queue()
    result = backend(tmp_path, queue).chat([{"role": "user", "content": "public fixture 😀"}])
    assert result.cost_usd == 0.001 and result.attempt_id == queue.requests[0]["call_id"]
    validate_request(queue.requests[0])
    record = json.loads(next(tmp_path.iterdir()).read_text(encoding="utf-8"))
    assert record["state"] == "completed"
    assert record["response"]["result"]["cost_usd"] == "0.001"


@pytest.mark.parametrize("outcome", ["unknown", "tampered", "timeout"])
def test_unknown_blocks_future_send_and_reconcile_never_sends(tmp_path, outcome):
    queue = Queue(outcome)
    client = backend(tmp_path, queue, wait_seconds=0.001, poll_seconds=0.001)
    with pytest.raises(QueueOutcomeUnknown):
        client.chat([{"role": "user", "content": "fixture"}])
    with pytest.raises(QueueOutcomeUnknown):
        client.chat([{"role": "user", "content": "second"}])
    assert len(queue.requests) == 1
    queue.outcome = "completed"
    assert client.reconcile(queue.requests[0]["call_id"]).cost_usd == 0.001
    assert len(queue.requests) == 1


def test_refusal_is_not_sent_but_shape_failure_retains_settled_usage(tmp_path):
    with pytest.raises(QueueAdmissionRefused):
        backend(tmp_path / "refused", Queue("refused")).chat([{"role": "user", "content": "x"}])
    with pytest.raises(ResponseShapeError) as exc:
        backend(tmp_path / "shape", Queue("shape")).chat([{"role": "user", "content": "x"}])
    assert exc.value.usage_result.cost_usd == 0.001
    assert json.loads(next((tmp_path / "shape").iterdir()).read_text(encoding="utf-8"))["state"] == "completed"


def test_bindings_change_approval_name_and_mutation_is_refused(tmp_path):
    original = backend(tmp_path / "a")
    for field, changed in [("owner", "umirbayulgaisha"), ("arm", "baseline"),
                           ("profile_sha256", "d" * 64), ("workflow_sha", "d" * 40),
                           ("framework_sha", "e" * 40)]:
        client = GitHubQueueBackend(config(), Queue(), {**IDENTITY, field: changed}, tmp_path / field)
        assert client.name != original.name and client.config.name == client.name
    other_queue = Queue()
    other_queue.binding = {**BINDING, "branch": "codex/p07-model-queue-other"}
    assert backend(tmp_path / "q", other_queue).name != original.name
    original.identity["arm"] = "baseline"
    with pytest.raises(QueueAdmissionRefused):
        original.chat([{"role": "user", "content": "x"}])


def test_config_transport_and_model_substitution_refused_before_send(tmp_path):
    queue = Queue()
    client = backend(tmp_path, queue)
    with pytest.raises(QueueAdmissionRefused):
        client.chat([{"role": "user", "content": "x"}], model="other")
    client.config.max_output_tokens = 256
    with pytest.raises(QueueAdmissionRefused):
        client.chat([{"role": "user", "content": "x"}])
    assert queue.requests == []
    with pytest.raises(ValueError):
        GitHubQueueBackend(config(api_key="forbidden-test-value"), queue, IDENTITY, tmp_path / "secret")
    with pytest.raises(ValueError):
        GitHubQueueBackend(config(), queue, {**IDENTITY, "owner": "other"}, tmp_path / "owner")


def test_bounds_and_message_schema_are_checked_before_queue_write(tmp_path):
    queue = Queue()
    client = backend(tmp_path, queue)
    for messages in [[{"role": "tool", "content": "x"}],
                     [{"role": "user", "content": "x" * MAX_WIRE_BYTES}],
                     [{"role": "user", "content": "x", "api_key": "forbidden"}]]:
        with pytest.raises(ValueError):
            client.chat(messages)
    assert not queue.requests and not list(tmp_path.iterdir())


def test_contents_transport_uses_only_fixed_scratch_branch_and_immutable_create():
    with pytest.raises(ValueError):
        GitHubContentsQueueTransport("example/scratch", "main", "test-token")
    client = GitHubContentsQueueTransport("example/scratch", BINDING["branch"], "test-token")
    assert client.binding == BINDING
    with pytest.raises(ValueError):
        client.get_response("../other")


def test_injected_contents_api_creates_once_without_token_in_data(tmp_path):
    queue = Queue()
    backend(tmp_path, queue).chat([{"role": "user", "content": "public fixture"}])
    req = queue.requests[0]
    values, writes = {}, []

    def api(method, path, body):
        key = path.split("?")[0]
        if method == "GET":
            raw = values.get(key)
            return None if raw is None else {"encoding": "base64", "content": raw}
        writes.append((path, body))
        values[key] = body["content"]
        return {"content": {"sha": "irrelevant-blob-sha"}}

    client = GitHubContentsQueueTransport("example/scratch", BINDING["branch"], "ephemeral-test-token", api_client=api)
    client.put_request(req["call_id"], req)
    client.put_request(req["call_id"], req)
    assert len(writes) == 1
    assert writes[0][1]["branch"] == BINDING["branch"]
    assert "ephemeral-test-token" not in json.dumps(writes)
    assert json.loads(base64.b64decode(writes[0][1]["content"])) == req
    changed = copy.deepcopy(req)
    changed["messages"][0]["content"] = "changed"
    changed["messages_sha256"] = digest(changed["messages"])
    del changed["request_sha256"]
    changed["request_sha256"] = digest(changed)
    with pytest.raises(QueueOutcomeUnknown):
        client.put_request(changed["call_id"], changed)
    assert len(writes) == 1


def test_corrupt_or_unknown_journal_blocks_new_request(tmp_path):
    client = backend(tmp_path)
    (tmp_path / "broken.json").write_text("not json", encoding="utf-8")
    with pytest.raises(QueueOutcomeUnknown):
        client.chat([{"role": "user", "content": "x"}])
