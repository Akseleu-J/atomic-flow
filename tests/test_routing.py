"""Offline fault matrix for RoutingBackend/QuotaStore: 429 failover, unknown holds, quota caps.

No network: routes are fake backends; the store uses a temporary directory. These pin
the contracts from docs/audit-2026-10-23/provider-routing.md (B01/B04/B05) and the
W1 exit gate "zero paid sends in free_only; unknown is never retried".

run_attempts invokes a shallow copy of the chosen route (per-attempt config), so call
evidence is recorded through a shared list, not an integer attribute.
"""
import time

import pytest

from atomic_flow.backends.base import ChatResult, ResponseShapeError
from atomic_flow.backends.http import HTTPStatusError, TransportError
from atomic_flow.backends.routing import QuotaStore, QuotaUnavailable, RoutingBackend
from atomic_flow.config import BackendConfig, ConversationConfig, RoutingConfig

MESSAGE = [{"role": "user", "content": "report the refund rule"}]


class Route:
    """A named route whose chat() raises queued exceptions or returns queued results."""

    def __init__(self, name, *, prices=(1, 1), outcome_queue=(), free_until=None, **config):
        self.name = name
        self.config = BackendConfig(model=f"model-{name}", base_url=f"https://{name}.example/v1", api_key="k",
                                    price_in_per_mtok=prices[0], price_out_per_mtok=prices[1],
                                    free_until=free_until, **config)
        self.queue = list(outcome_queue)
        self.log = []

    @property
    def calls(self):
        return len(self.log)

    def chat(self, messages, **kwargs):
        self.log.append(self.name)
        outcome = self.queue.pop(0) if self.queue else ChatResult("ok", 10, 5, 0.001, 1, cost_source="provider")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def throttled(retry_after="120"):
    error = HTTPStatusError(429, "Too Many Requests")
    error.retry_after = retry_after
    return error


def routing(routes, *, mode="capped_paid", store=None, **settings):
    return RoutingBackend(routes, RoutingConfig(mode=mode, **settings), ConversationConfig(), store=store)


def test_first_route_success_attributed_per_attempt(tmp_path):
    a, b = Route("a"), Route("b")
    result = routing([a, b], store=QuotaStore(tmp_path)).run_attempts(
        lambda selected: selected.chat(MESSAGE), MESSAGE)
    assert result.resolved_backend == "a" and result.resolved_model == "model-a"
    assert result.attempt_id.startswith("route_")
    assert a.calls == 1 and b.calls == 0


def test_confirmed_429_fails_over_and_persists_provider_cooldown(tmp_path):
    store = QuotaStore(tmp_path)
    a = Route("a", outcome_queue=[throttled("120")])
    b = Route("b")
    result = routing([a, b], store=store, cooldown_seconds=60).run_attempts(
        lambda selected: selected.chat(MESSAGE), MESSAGE)
    assert a.calls == 1 and b.calls == 1 and result.resolved_backend == "b"
    with store.state("a.example") as state:  # scope defaults to the endpoint netloc
        assert state["cooldown_until"] >= time.time() + 119
        assert state["pending"] == {}


def test_unknown_outcome_is_never_failed_over_and_blocks_until_reconciled(tmp_path):
    store = QuotaStore(tmp_path)
    a = Route("a", outcome_queue=[TransportError("connection lost")])
    b = Route("b")
    with pytest.raises(TransportError):
        routing([a, b], store=store).run_attempts(lambda selected: selected.chat(MESSAGE), MESSAGE)
    assert a.calls == 1 and b.calls == 0
    # The unknown attempt still holds the route's concurrency slot: no silent retry.
    with pytest.raises(QuotaUnavailable):
        routing([Route("a")], store=store).run_attempts(lambda selected: selected.chat(MESSAGE), MESSAGE)
    with store.state("a.example") as state:
        attempt = next(iter(state["pending"]))
    evidence = "f" * 64
    store.reconcile("a.example", attempt, evidence_sha256=evidence)
    store.reconcile("a.example", attempt, evidence_sha256=evidence)  # idempotent
    assert routing([Route("a")], store=store).run_attempts(
        lambda selected: selected.chat(MESSAGE), MESSAGE).content == "ok"


def test_reconcile_rejects_unknown_attempt_and_bad_evidence(tmp_path):
    store = QuotaStore(tmp_path)
    with pytest.raises(ValueError):
        store.reconcile("a.example", "route_missing", evidence_sha256="0" * 64)
    with pytest.raises(ValueError):
        store.reconcile("a.example", "route_x", evidence_sha256="not-hex")


def test_completed_shape_error_is_not_failed_over(tmp_path):
    store = QuotaStore(tmp_path)
    a = Route("a", outcome_queue=[ResponseShapeError("malformed", ChatResult("", 1, 1, 0.001, 1))])
    b = Route("b")
    with pytest.raises(ResponseShapeError):
        routing([a, b], store=store).run_attempts(lambda selected: selected.chat(MESSAGE), MESSAGE)
    assert a.calls == 1 and b.calls == 0  # a completed response never switches route


def test_free_only_skips_paid_expired_and_unconfirmed_free_routes(tmp_path):
    store = QuotaStore(tmp_path)
    paid = Route("paid", prices=(1, 1))
    expired = Route("expired", prices=(0, 0), free_until=time.time() - 10)
    unconfirmed = Route("unconfirmed", prices=(0, 0))            # no free_until: never assumed free
    with pytest.raises(QuotaUnavailable):
        routing([paid, expired, unconfirmed], mode="free_only", store=store).run_attempts(
            lambda selected: selected.chat(MESSAGE), MESSAGE)
    assert not paid.calls and not expired.calls and not unconfirmed.calls

    confirmed = Route("confirmed", prices=(0, 0), free_until=time.time() + 3600,
                      outcome_queue=[ChatResult("ok", 10, 5, 0.0, 1, cost_source="provider")])
    result = routing([paid, confirmed], mode="free_only", store=QuotaStore(tmp_path / "b")).run_attempts(
        lambda selected: selected.chat(MESSAGE), MESSAGE)
    assert result.resolved_backend == "confirmed" and not paid.calls


def test_unknown_prices_route_is_skipped(tmp_path):
    unpriced = Route("unpriced", prices=(None, None))
    priced = Route("priced")
    result = routing([unpriced, priced], store=QuotaStore(tmp_path)).run_attempts(
        lambda selected: selected.chat(MESSAGE), MESSAGE)
    assert result.resolved_backend == "priced" and unpriced.calls == 0


def test_rate_limit_cap_blocks_second_attempt_in_same_window(tmp_path):
    store = QuotaStore(tmp_path)
    a = Route("a", requests_per_minute=1)
    result = routing([a], store=store).run_attempts(lambda selected: selected.chat(MESSAGE), MESSAGE)
    assert result.content == "ok"
    # The stored window counter, not the in-memory route object, enforces the cap.
    with pytest.raises(QuotaUnavailable):
        routing([Route("a", requests_per_minute=1)], store=store).run_attempts(
            lambda selected: selected.chat(MESSAGE), MESSAGE)


def test_max_attempts_and_deadline_bound_the_whole_chain(tmp_path):
    store = QuotaStore(tmp_path / "x")
    a = Route("a", outcome_queue=[throttled("1")])
    b = Route("b", outcome_queue=[throttled("1")])
    with pytest.raises(QuotaUnavailable):
        routing([a, b], store=store, max_attempts=2, cooldown_seconds=1).run_attempts(
            lambda selected: selected.chat(MESSAGE), MESSAGE)
    # Each route is offered at most once per logical turn; attempts are physical sends.
    assert a.calls == 1 and b.calls == 1

    store2 = QuotaStore(tmp_path / "y")
    slow = Route("slow", outcome_queue=[throttled("300")])
    with pytest.raises(QuotaUnavailable):
        routing([slow], store=store2, timeout_seconds=0.5).run_attempts(
            lambda selected: selected.chat(MESSAGE), MESSAGE)
    assert slow.calls == 1  # the refusal was observed; its 300 s cooldown exceeds the deadline


def test_overrides_and_tools_are_refused(tmp_path):
    router = routing([Route("a")], store=QuotaStore(tmp_path))
    with pytest.raises(Exception, match="model override"):
        router.run_attempts(lambda selected: selected.chat(MESSAGE), MESSAGE, model="other")
    with pytest.raises(Exception, match="text_json"):
        router.run_attempts(lambda selected: selected.chat(MESSAGE), MESSAGE, tools=[{"type": "function"}])


def test_context_limit_skips_to_next_route(tmp_path):
    tiny = Route("tiny", max_input_tokens=1)
    roomy = Route("roomy")
    result = routing([tiny, roomy], store=QuotaStore(tmp_path)).run_attempts(
        lambda selected: selected.chat(MESSAGE), MESSAGE)
    assert result.resolved_backend == "roomy" and tiny.calls == 0
