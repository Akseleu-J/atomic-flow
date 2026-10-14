import pytest
from conftest import ScriptedBackend, alias_of

from atomic_flow.errors import BackendError, BudgetExceeded
from atomic_flow.llm.orchestrator import Orchestrator, _looks_like_action, choose_mode, normalize_scope, parse_json
from atomic_flow.llm.schemas import Answer, QueryIntent

OK = {"verdict": "ok", "unsupported_claims": [], "missing_node_ids": [], "feedback": "", "citation_coverage": 1.0}


def answer_for(label, text, **extra):
    return lambda user: {"text": text, "citations": [{"node_id": alias_of(user, label), "what": "rule"}],
                         "confidence": "high", **extra}


REFUND = "invoice_service.py::is_refundable"


def test_simple_mode_single_call(project, config):
    backend = ScriptedBackend({"answerer": answer_for(REFUND, "`is_refundable` needs status paid and 0-30 days.")})
    result = Orchestrator(config, project, backend).answer("What makes an order refundable?", mode="simple")
    assert backend.roles() == ["answerer"] and result.citations[0].node_id == REFUND
    assert result.verdict == "unverified" and result.grounding == 1.0 and result.cost_usd == pytest.approx(0.001)


def test_medium_flow_and_alias_citations(project, config):
    backend = ScriptedBackend({"parser": {"task_type": "explain", "focus_terms": ["refund"], "reasoning": "extra key is ignored"},
                               "answerer": answer_for(REFUND, "`is_refundable` needs paid."), "critic": OK})
    result = Orchestrator(config, project, backend).answer("What exact rule makes an order refundable?", mode="medium")
    assert backend.roles() == ["parser", "answerer", "critic"] and result.verdict == "ok"
    assert (project.parent / "home" / "runs" / result.ledger_id / "meta.json").exists()


def test_json_repair_round(project, config):
    good = {"text": "x", "citations": [], "confidence": "low"}
    backend = ScriptedBackend({"answerer": ["not json at all", good]})
    result = Orchestrator(config, project, backend).answer("refundable?", mode="simple")
    assert result.text == "x" and len(backend.calls) == 2
    assert "Rejected" in backend.calls[1][1][-1]["content"]


def test_hallucinated_identifier_downgrades_ok_and_retries(project, config):
    bad = answer_for(REFUND, "`is_refundable` calls `ghost_function` internally.")
    good = answer_for(REFUND, "`is_refundable` checks paid and 30 days.")
    backend = ScriptedBackend({"parser": {}, "answerer": [bad, good], "critic": OK})
    result = Orchestrator(config, project, backend).answer("What makes an order refundable?", mode="medium")
    assert result.attempts == 2 and result.verdict == "ok" and result.unknown_identifiers == []


def test_invalid_citation_is_dropped_and_reported(project, config):
    backend = ScriptedBackend({"answerer": {"text": "ok", "citations": [{"node_id": "n99999"}, {"node_id": "nope.py::x"}]}})
    result = Orchestrator(config, project, backend).answer("refundable?", mode="simple")
    assert result.citations == [] and result.dropped_citations == 2 and result.warnings


def test_hard_mode_expands_and_renders_selected_nodes(project, config):
    def refiner(user):
        return {"node_ids": [alias_of_any(user, "refund_policy.py::refund_message")], "rationale": "r"}

    def expander(user):
        return {"node_ids": [alias_of_any(user, REFUND)], "rationale": "callee"}

    backend = ScriptedBackend({
        "parser": {}, "refiner": refiner, "expander": expander,
        "answerer": [{"text": "`refund_message` returns approved", "citations": [], "confidence": "low"},
                     lambda user: {"text": "`refund_message` calls `is_refundable`", "confidence": "high",
                                   "citations": [{"node_id": alias_of(user, REFUND)}]}],
        "critic": [{**OK, "verdict": "needs_more_context", "feedback": "show the callee"}, OK]})
    result = Orchestrator(config, project, backend).answer("Review how refund_message decides", mode="hard")
    assert backend.roles() == ["parser", "refiner", "answerer", "critic", "expander", "answerer", "critic"]
    assert result.verdict == "ok" and result.citations[0].node_id == REFUND


def alias_of_any(user_text, label):
    import re
    path, symbol = label.split("::")
    match = re.search(rf'"id": "(n\d+)",\s*"path": "{re.escape(path)}",\s*"symbol": "{re.escape(symbol)}"', user_text)
    assert match, label
    return match.group(1)


def test_weak_retrieval_in_simple_mode_calls_parser(project, config):
    backend = ScriptedBackend({
        "parser": lambda user: {"task_type": "other", "focus_terms": []},
        "answerer": lambda user: {"text": "ok", "confidence": "low"},
    })
    result = Orchestrator(config, project, backend).answer("zzz_nonexistent_symbol_qqq", mode="simple")
    assert backend.roles() == ["parser", "answerer"] and result.roles_invoked == ["parser", "answerer"]


def test_auto_refresh_catches_edits(project, config):
    (project / "invoice_service.py").write_text((project / "invoice_service.py").read_text().replace("30", "45"))
    backend = ScriptedBackend({"answerer": lambda user: (_ for _ in ()).throw(AssertionError) if "<= 30" in user else
                               {"text": "`is_refundable` uses 45", "confidence": "high"}})
    assert Orchestrator(config, project, backend).answer("refundable window?", mode="simple").text.endswith("45")


def test_budget_blocks_calls(project, config):
    config.budget.daily_limit_usd = 0.0005
    backend = ScriptedBackend({"parser": {}, "answerer": {"text": "x"}, "critic": OK})
    with pytest.raises(BudgetExceeded):
        Orchestrator(config, project, backend).answer("refundable?", mode="medium")


def test_request_id_validation(project, config):
    with pytest.raises(ValueError):
        Orchestrator(config, project, ScriptedBackend({})).answer("q", mode="simple", request_id="../../evil")


def test_scope_normalization_keeps_dotfiles():
    assert normalize_scope("./.github/ci.yml") == ".github/ci.yml" and normalize_scope("\\src\\a.py") == "src/a.py"


def test_choose_mode():
    assert choose_mode("review the flow", "medium") == "hard"
    assert choose_mode("explain this", "medium", file_scope="a.py") == "simple"


def test_action_keywords_are_detected_deterministically():
    assert _looks_like_action("Rename the parameter 'root' in refresh_tree to 'project_root'")
    assert _looks_like_action("Fix the refund check and update the tests")
    assert not _looks_like_action("What does refresh_tree do?")
    assert QueryIntent(task_type="rename").task_type == "modify"


def test_parse_json_variants():
    assert parse_json('```json\n{"text": "a"}\n```', Answer).text == "a"
    assert parse_json('Sure! {"text": "a", "confidence": "bogus"} done', Answer).confidence == "medium"
    with pytest.raises(BackendError):
        parse_json("[1, 2]", Answer)
