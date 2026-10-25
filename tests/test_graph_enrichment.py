"""Scripted P03 acceptance: invalidation, identity, evidence, atomic failure."""
import copy

import pytest

from atomic_flow.graph.enrichment import SemanticBudget, enrich
from atomic_flow.graph.schema import GraphContractError

IDENTITY = dict(extractor="ast1", prompt="p1", model="scripted1", config="c1", language="ru")
FILES = {"a.py": b"VALUE = 1\n", "b.py": b"OTHER = 2\n"}


def analyzer(request):
    return {"summary": "Scripted explanation", "tags": ["fixture"], "evidence": [
        {"node_id": "file:" + request["path"], "start_line": 1, "end_line": 1,
         "quote": request["source"].splitlines(keepends=True)[0]}]}


def run(files, cache, **kwargs):
    return enrich(files, project_scope=kwargs.pop("project_scope", "private/project-A"),
                  identity=kwargs.pop("identity", IDENTITY), analyzer=kwargs.pop("analyzer", analyzer),
                  budget=kwargs.pop("budget", SemanticBudget(10)), cache=cache, **kwargs)


def test_repeat_reuses_cache_body_and_document_edits_invalidate_locally():
    cache = {}
    assert run(FILES, cache)["calls"] == 2
    assert run(FILES, cache)["calls"] == 0
    changed = dict(FILES, **{"a.py": b"VALUE = 3\n# doc edit\n"})
    result = run(changed, cache)
    assert result["calls"] == 1
    assert result["review"]["semantic_correctness"] == "not_assessed"
    assert result["artifacts"]["b.py"]["cache_key"] == run(FILES, cache)["artifacts"]["b.py"]["cache_key"]


@pytest.mark.parametrize("field", list(IDENTITY))
def test_every_identity_field_invalidates(field):
    cache = {}
    run(FILES, cache)
    assert run(FILES, cache, identity={**IDENTITY, field: "changed"})["calls"] == 2


def test_scope_dependencies_and_new_files_invalidate():
    cache = {}
    run(FILES, cache, dependencies={"a.py": ["b.py"]})
    changed = dict(FILES, **{"b.py": b"OTHER = 4\n"})
    assert run(changed, cache, dependencies={"a.py": ["b.py"]})["calls"] == 2
    assert run(FILES, cache, project_scope="private/project-B")["calls"] == 2
    assert run(dict(FILES, **{"c.py": b"C = 1\n"}), cache)["calls"] == 2  # a's prior key had dependency b


def test_failure_never_publishes_partial_cache_and_budget_is_not_refunded():
    cache, budget = {}, SemanticBudget(1)
    with pytest.raises(GraphContractError, match="budget"):
        run(FILES, cache, budget=budget)
    assert not cache and budget.calls == 1
    def fabricated(request):
        result = analyzer(request)
        result["evidence"][0]["quote"] = "fabrication"
        return result
    with pytest.raises(GraphContractError, match="exact current source"):
        run(FILES, cache, analyzer=fabricated)
    assert not cache


def test_cached_evidence_is_revalidated_and_cancel_does_not_publish():
    from atomic_flow.agents.execution import TaskCancelled
    cache = {}
    run(FILES, cache)
    damaged = copy.deepcopy(cache)
    for value in damaged.values():
        value["evidence"][0]["quote"] = "stale"
    with pytest.raises(GraphContractError):
        run(FILES, damaged)
    def cancelled(request):
        raise TaskCancelled("stop")
    with pytest.raises(TaskCancelled):
        run(FILES, {}, analyzer=cancelled)


def test_sdk_uses_admission_and_reports_unsupported_omissions(tmp_path):
    from atomic_flow.sdk import understand_project
    (tmp_path / "a.py").write_bytes(FILES["a.py"])
    (tmp_path / "README.md").write_bytes(b"# fixture\n")
    (tmp_path / "app.ts").write_bytes(b"const x = 1;\n")
    (tmp_path / "credentials.json").write_bytes(b'{"token":"private-value"}')
    result = understand_project(tmp_path, project_scope="sdk-fixture", identity=IDENTITY,
                                analyzer=analyzer, budget=SemanticBudget(2), cache={})
    assert sorted(result["artifacts"]) == ["README.md", "a.py"]
    assert result["review"]["omitted_unsupported_adapters"] == ["app.ts"]


def test_analyzer_receives_redacted_source_and_symbol_span_is_checked():
    captured = []
    def inspect(request):
        captured.append(request["source"])
        return analyzer(request)
    run({"a.py": b'VALUE = 1\napi_key = "private-secret-value-123456"\n'}, {}, analyzer=inspect)
    assert "private-secret-value-123456" not in captured[0]
    def wrong_span(request):
        result = analyzer(request)
        result["evidence"][0]["node_id"] = "sym:a.py::f"
        return result
    with pytest.raises(GraphContractError, match="outside cited node"):
        run({"a.py": b"VALUE = 1\ndef f():\n    return 2\n"}, {}, analyzer=wrong_span)


def test_sdk_source_drift_refuses_cache_publication(tmp_path):
    from atomic_flow.sdk import understand_project
    path = tmp_path / "a.py"
    path.write_bytes(FILES["a.py"])
    cache = {}
    def drifting(request):
        path.write_bytes(b"VALUE = 9\n")
        return analyzer(request)
    with pytest.raises(GraphContractError, match="source changed"):
        understand_project(tmp_path, project_scope="drift", identity=IDENTITY,
                           analyzer=drifting, budget=SemanticBudget(1), cache=cache)
    assert not cache
