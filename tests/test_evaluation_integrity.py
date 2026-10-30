"""Frozen positive/negative grader calibration and parsing-independent usage."""
import json
from types import SimpleNamespace

import pytest

from atomic_flow.backends.base import ChatResult
from atomic_flow.benchmarking import _evaluate, benchmark_cases, run_benchmark
from atomic_flow.config import AtomicConfig, BackendConfig
from atomic_flow.errors import BudgetExceeded
from atomic_flow.evaluation_meter import EvaluationMeter, usage_summary
from atomic_flow.qa_rubric import CALIBRATED_ABSTENTIONS, CALIBRATED_ANSWERS, RUBRIC_VERSION

CASES = {case["id"]: case for case in benchmark_cases()}


def grade(case_id, text, *, confidence="high", citations=None):
    case = CASES[case_id]
    return _evaluate(case, SimpleNamespace(text=text, confidence=confidence, missing_context=[], unknown_identifiers=[],
        citations=[SimpleNamespace(node_id=value) for value in (case["expected_nodes"] if citations is None else citations)]),
        case["expected_nodes"])


@pytest.mark.parametrize("case_id", sorted(CALIBRATED_ANSWERS))
def test_reviewed_complete_answers_calibrate_positive_and_irrelevant_citations_fail(case_id):
    for text in CALIBRATED_ANSWERS[case_id]:
        result = grade(case_id, text)
        assert result["passed"], (case_id, text, result)
        assert result["rubric_version"] == RUBRIC_VERSION
        assert not grade(case_id, text, citations=["unrelated.py::unrelated"])["passed"]


@pytest.mark.parametrize("text", [
    "The subtotal subtracts price from quantity.",
    "The subtotal divides price by quantity.",
    "The subtotal is not the sum of price times quantity for all invoice items.",
    "Sum price times quantity. Also, payments are processed instantly.",
])
def test_semantic_inversions_and_appended_fabrications_cannot_auto_pass(text):
    assert not grade("invoice-subtotal", text)["passed"]


@pytest.mark.parametrize("text", [
    "Payments are processed instantly.",
    "No information is shown, but payments are processed instantly.",
    "No information about bank payouts is shown in the supplied source. Payments are processed instantly.",
])
def test_low_confidence_and_partial_disclaimer_are_not_successful_abstentions(text):
    assert not grade("unanswerable-payout", text, confidence="low")["passed"]


def test_calibrated_abstentions_and_uncalibrated_cases_are_explicit():
    for text in CALIBRATED_ABSTENTIONS:
        result = grade("unanswerable-payout", text, confidence="low")
        assert result["passed"] and result["abstained"]
    result = grade("sourcing-pipeline", "ManagerAgent runs the pipeline")
    assert result["semantic_status"] == "needs_review" and not result["passed"]
    assert not grade("invoice-subtotal", "sum price quantity")["passed"]


class Backend:
    name = "scripted"
    config = BackendConfig(max_retries=0, max_output_tokens=64)
    def chat(self, *args, **kwargs):
        return ChatResult("malformed after charging", tokens_in=10, tokens_out=5, cost_usd=0.1)


@pytest.mark.parametrize("unknown", [False, True])
def test_legacy_report_counts_failed_parse_and_preserves_attempts_before_parsing(tmp_path, monkeypatch, unknown):
    from atomic_flow.llm.schemas import Answer
    output = tmp_path / "report.json"
    backend = Backend()
    if unknown:
        def broken(*args, **kwargs):
            raise RuntimeError("provider secret must not be reported")
        backend.chat = broken
    monkeypatch.setattr("atomic_flow.benchmarking.make_backend", lambda *args: backend)
    class Orchestrator:
        calls = 0
        def __init__(self, config, root, backend):
            self.backend = backend
        def answer(self, *args, **kwargs):
            type(self).calls += 1
            self.backend.chat([])
            ledger = json.loads(output.with_name(output.name + ".attempts.json").read_text())
            assert len(ledger["calls"]) == self.calls and ledger["cost_complete"]
            if self.calls == 2:
                raise ValueError("failed parse after charged response")
            answer = Answer(text="Sum price times quantity", citations=[{"node_id": "invoice_service.py::Invoice.subtotal"}])
            return SimpleNamespace(**{**answer.model_dump(), "citations": answer.citations}, grounding=1, ledger_id="scripted", verdict="ok", latency_ms=1,
                                   unknown_identifiers=[])
    monkeypatch.setattr("atomic_flow.benchmarking.Orchestrator", Orchestrator)
    report = run_benchmark(AtomicConfig(), limit=2, output=output)
    saved = json.loads(output.with_name(output.name + ".attempts.json").read_text())
    if unknown:
        assert report["summary"]["cost_usd"] is None and report["summary"]["tokens_in"] is None
        assert report["summary"]["known_cost_usd"] == 0 and report["stopped_early"]
        assert len(saved["calls"]) == 1 and saved["calls"][0]["request_outcome"] == "unknown"
        assert "provider secret" not in json.dumps(report) + json.dumps(saved)
    else:
        assert report["summary"]["cost_usd"] == 0.2 and report["summary"]["tokens_in"] == 20
        assert report["results"][1]["error_type"] == "ValueError" and report["results"][1]["cost_usd"] == 0.1
        assert report["summary"]["cost_per_success_usd"] == 0.2
        assert len(saved["calls"]) == 2 and saved["cost_complete"]
    with pytest.raises(ValueError, match="already exists"):
        run_benchmark(AtomicConfig(), limit=2, output=output)


def test_meter_distinguishes_refusal_unknown_and_failed_persistence():
    class Refusal(RuntimeError):
        request_outcome = "not_sent"
    backend = Backend()
    def refuse(*args, **kwargs):
        raise Refusal()
    backend.chat = refuse
    meter = EvaluationMeter(backend)
    with pytest.raises(Refusal):
        meter.chat([])
    assert usage_summary(meter.calls)["reported_cost_usd"] == 0
    meter = EvaluationMeter(Backend(), on_update=lambda *args: (_ for _ in ()).throw(OSError("storage unavailable")))
    with pytest.raises(OSError):
        meter.chat([])
    assert meter.calls[0]["status"] == "in_flight" and not usage_summary(meter.calls)["cost_complete"]
    with pytest.raises(BudgetExceeded):
        meter.chat([])


def test_native_calls_are_observed_by_the_same_meter():
    class Native(Backend):
        def chat_tools(self, *args, **kwargs):
            return self.chat(*args, **kwargs)
    meter = EvaluationMeter(Native())
    meter.chat_tools([], tools=[])
    assert usage_summary(meter.calls)["reported_cost_usd"] == 0.1
    assert not hasattr(EvaluationMeter(Backend()), "chat_tools")
