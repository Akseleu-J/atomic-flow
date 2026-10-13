import os
import stat

import pytest
import yaml

from atomic_flow.config import load_config, trust_project
from atomic_flow.errors import BudgetExceeded
from atomic_flow.kaggle.credentials import prepare_secret_file
from atomic_flow.llm.prompts import PromptRegistry


def test_untrusted_project_cannot_disable_budget(tmp_path):
    (tmp_path / ".atomic_flow").mkdir()
    (tmp_path / ".atomic_flow" / "config.yaml").write_text(
        yaml.safe_dump({"budget": {"daily_limit_usd": 0}}), encoding="utf-8"
    )
    assert load_config(tmp_path).budget.daily_limit_usd == 10.0
    trust_project(tmp_path)
    assert load_config(tmp_path).budget.daily_limit_usd == 0


def test_project_prompts_need_trust(project):
    prompts = project / ".atomic_flow" / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "answerer.md").write_text("EVIL", encoding="utf-8")
    registry = PromptRegistry(project)
    assert registry.get("answerer") != "EVIL" and registry.sources["answerer"] == "builtin"
    trust_project(project)
    registry = PromptRegistry(project)
    assert registry.get("answerer") == "EVIL" and registry.sources["answerer"] == "project"


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_prepare_secret_file_tightens_existing_file(tmp_path):
    path = tmp_path / "kaggle.env"
    path.write_text("x")
    path.chmod(0o644)
    prepare_secret_file(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_ledger_is_per_day_and_rechecks(tmp_path):
    from atomic_flow.budget import SpendLedger

    ledger = SpendLedger(0.01, tmp_path)
    ledger.check()
    ledger.record(cost_usd=0.02)
    with pytest.raises(BudgetExceeded):
        ledger.check()
    assert ledger.path.parent == tmp_path
    assert ledger.path.suffix == ".jsonl"


def test_split_conversation_handles_trailing_assistant():
    pytest.importorskip("fastapi")
    from atomic_flow.api.server import split_conversation

    turns = [("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2")]
    last, history = split_conversation(turns)
    assert last == "q2"
    assert history.splitlines() == ["USER: q1", "ASSISTANT: a1"]


def test_split_conversation_no_user():
    pytest.importorskip("fastapi")
    from atomic_flow.api.server import split_conversation

    assert split_conversation([("assistant", "hi")]) == (None, "")


def test_docs_hidden_when_server_key_set(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import atomic_flow.api.server as server

    monkeypatch.setenv("ATOMIC_FLOW_SERVER_API_KEY", "k" * 32)
    client = TestClient(server.create_app())
    assert client.get("/docs").status_code == 404
    assert client.get("/health").status_code == 200


def test_project_locks_are_per_project(tmp_path):
    from atomic_flow.llm.orchestrator import project_lock

    assert project_lock(tmp_path / "a") is project_lock(tmp_path / "a")
    assert project_lock(tmp_path / "a") is not project_lock(tmp_path / "b")


def test_benchmark_min_citations_is_respected():
    from atomic_flow.benchmarking import _evaluate
    from atomic_flow.llm.schemas import Answer, Citation

    case = {"id": "synthetic", "query": "x", "expected_nodes": ["a.py::foo", "a.py::bar"],
            "min_citations": 1, "must_match": []}
    answer = Answer(text="Some answer.", citations=[Citation(node_id="a.py::foo", what="w")], confidence="medium")
    result = _evaluate(case, answer, case["expected_nodes"])
    assert result["citation_hit"] is True
    assert result["citation_strict"] is False
