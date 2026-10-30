from __future__ import annotations

import io
import json
import tarfile
from types import SimpleNamespace

import pytest

from atomic_flow.agents.execution import CommandResult, ExecutionUnavailable
from atomic_flow.backends.base import ChatResult
from atomic_flow.config import AtomicConfig, BackendConfig, ExecutionConfig
from atomic_flow.errors import BudgetExceeded
from atomic_flow.evaluation import check_snapshot, export_snapshot, load_suite
from atomic_flow.qa_evaluation import CallMeter, run_qa_comparison, summarize


def test_manifest_rejects_traversal_and_duplicate_ids(tmp_path):
    path = tmp_path / "suite.json"
    suite = {"schema_version": 1, "base_commit": "a" * 40, "gold_commit": "b" * 40,
             "cases": [{"id": "safe", "check": "check", "request": "Fix", "paths": ["../outside.py"]}]}
    path.write_text(json.dumps(suite))
    with pytest.raises(ValueError, match="Unsafe"):
        load_suite(path)
    suite["cases"][0]["paths"] = ["src/a.py"]
    suite["cases"] *= 2
    path.write_text(json.dumps(suite))
    with pytest.raises(ValueError, match="unique"):
        load_suite(path)


@pytest.mark.parametrize("name,link", [("../escape", False), ("safe", True)])
def test_archive_rejects_unsafe_members_before_writing(tmp_path, monkeypatch, name, link):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        first = tarfile.TarInfo("ordinary.py")
        first.size = 1
        archive.addfile(first, io.BytesIO(b"x"))
        unsafe = tarfile.TarInfo(name)
        if link:
            unsafe.type, unsafe.linkname = tarfile.SYMTYPE, "/etc/passwd"
        archive.addfile(unsafe)
    monkeypatch.setattr("atomic_flow.evaluation.subprocess.run", lambda *a, **kw: SimpleNamespace(stdout=data.getvalue()))
    with pytest.raises(ValueError):
        export_snapshot(tmp_path, "a" * 40, tmp_path / "snapshot")
    assert not (tmp_path / "snapshot").exists()


def test_missing_engine_is_unavailable_and_never_passes(tmp_path, monkeypatch):
    def unavailable(*a, **kw):
        raise ExecutionUnavailable("no Docker")
    monkeypatch.setattr("atomic_flow.evaluation.run_python", unavailable)
    assert check_snapshot(tmp_path, "", "test", ExecutionConfig(mode="docker"))["status"] == "unavailable"


@pytest.mark.parametrize("output,code,status", [
    ('{"grader_status":"pass"}', 1, "error"),
    ('{"grader_status":"fail","error_type":"ModuleNotFoundError","detail":"No module named pydantic"}', 1, "error"),
    ('{"grader_status":"fail","error_type":"AssertionError"}', 1, "fail"),
])
def test_bad_exit_or_dependency_failure_is_not_a_calibrated_result(tmp_path, monkeypatch, output, code, status):
    monkeypatch.setattr("atomic_flow.evaluation.run_python", lambda *a, **kw: CommandResult(code, output, ""))
    assert check_snapshot(tmp_path, "", "test", ExecutionConfig())["status"] == status


class Backend:
    config = BackendConfig(max_retries=0)
    name = "fake"

    def chat(self, *a, **kw):
        return ChatResult("not valid JSON", tokens_in=10, tokens_out=3, cost_usd=0.01)


def test_meter_counts_usage_before_parsing_and_enforces_call_cap():
    meter = CallMeter(Backend(), max_calls=1)
    assert meter.chat([]).content == "not valid JSON"
    assert meter.calls[0]["tokens_in"] == 10
    with pytest.raises(BudgetExceeded):
        meter.chat([])
    report = summarize([{"calls": meter.calls, "passed": False, "wall_seconds": 1}])
    assert report["reported_cost_usd"] == 0.01 and report["cost_per_success_usd"] is None


def test_unknown_error_cost_blocks_further_calls_and_summary_is_unknown():
    class Broken(Backend):
        def chat(self, *a, **kw):
            raise RuntimeError("do not store this secret")
    meter = CallMeter(Broken())
    with pytest.raises(RuntimeError):
        meter.chat([])
    with pytest.raises(BudgetExceeded, match="Unknown"):
        meter.chat([])
    assert "secret" not in json.dumps(meter.calls)
    assert summarize([{"calls": meter.calls, "passed": False, "wall_seconds": 1}])["reported_cost_usd"] is None


def test_paired_report_preserves_cost_of_failed_parse_and_checkpoints_both_trials(tmp_path, monkeypatch):
    class AnswerBackend(Backend):
        def chat(self, *a, **kw):
            return ChatResult(json.dumps({"text": "Sum price times quantity", "citations": [
                {"node_id": "invoice_service.py::Invoice.subtotal"}]}), tokens_in=10, tokens_out=5, cost_usd=0.01)

    class BrokenAtomic:
        def __init__(self, config, project, backend):
            self.backend = backend

        def answer(self, *a, **kw):
            self.backend.chat([])
            raise ValueError("simulated parse failure after paid response")

    monkeypatch.setattr("atomic_flow.qa_evaluation.Orchestrator", BrokenAtomic)
    output = tmp_path / "report.json"
    report = run_qa_comparison(AnswerBackend(), AtomicConfig(), case_ids=["invoice-subtotal"], output=output)
    assert report["summary"]["full_context"]["passed"] == 1
    assert report["summary"]["atomic_medium"]["passed"] == 0
    assert report["summary"]["atomic_medium"]["reported_cost_usd"] == 0.01
    assert json.loads(output.read_text())["summary"] == report["summary"]
