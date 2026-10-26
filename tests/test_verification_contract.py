"""Reject reports that overstate measurements or carry unknown wire fields."""

import pytest
from pydantic import ValidationError

from atomic_flow.llm.verification import VerificationReport


def test_empty_context_cannot_claim_complete_source_coverage():
    report = VerificationReport(context_coverage={"status": "not_assessed", "shown_nodes": 0})
    assert report.context_coverage.status == "not_assessed"
    with pytest.raises(ValidationError):
        VerificationReport(context_coverage={"status": "shown_sources_complete", "shown_nodes": 0})


def test_no_checks_are_not_assessed_and_wire_roundtrips():
    report = VerificationReport()
    assert report["status"] == "not_assessed"
    assert report.score is None
    assert VerificationReport.model_validate_json(report.model_dump_json()) == report


@pytest.mark.parametrize("fields", [
    {"score": 1.0},
    {"status": "passed"},
    {"checked": True},
    {"checked": 1, "passed": 0, "failed": 0},
    {"checked": 2, "passed": 1, "failed": 1, "score": 1.0, "status": "failed"},
    {"semantic_correctness": "passed"},
    {"unexpected": "authority"},
    {"citation_spans": {"status": "passed", "checked": 1, "items": []}},
])
def test_incoherent_or_unknown_measurement_is_rejected(fields):
    with pytest.raises(ValidationError):
        VerificationReport.model_validate(fields)


def test_measured_identifiers_and_exact_source_spans_are_preserved():
    report = VerificationReport.model_validate({
        "checked": 2, "passed": 1, "failed": 1,
        "score": 0.5, "status": "failed",
        "citation_spans": {
            "status": "passed", "checked": 1,
            "items": [{"node_id": "n", "path": "x.py", "source_digest": "a" * 40,
                       "status": "passed", "lines": "2-3", "start_line": 2,
                       "end_line": 3, "quote": "def f():\n    return 1"}],
        },
    })
    assert report.citation_spans.items[0].quote == "def f():\n    return 1"


def test_score_uses_the_published_three_decimal_precision():
    fields = {"checked": 3, "passed": 2, "failed": 1, "status": "failed"}
    assert VerificationReport.model_validate({**fields, "score": 0.667}).score == 0.667
    with pytest.raises(ValidationError):
        VerificationReport.model_validate({**fields, "score": 0.5})
