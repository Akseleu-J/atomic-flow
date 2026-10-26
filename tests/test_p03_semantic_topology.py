"""Semantic reuse must cover dependencies newly admitted since enrichment."""
import pytest

from atomic_flow.graph.enrichment import SemanticBudget
from atomic_flow.graph.operations import graph_operation
from atomic_flow.graph.schema import GraphContractError
from atomic_flow.sdk import understand_project

IDENTITY = dict(extractor="ast", prompt="frozen", model="scripted", config="fixed", language="en")


def _analyze(request):
    return {"summary": "Source explanation", "tags": [], "evidence": [{
        "node_id": "file:" + request["path"], "start_line": 1, "end_line": 1,
        "quote": request["source"].splitlines(keepends=True)[0]}]}


@pytest.mark.parametrize("consumer,added", [
    (b"from provider import helper\ndef use(): return helper()\n",
     b"def helper(): return 1\n"),
    (b"def use(): return 1\n", b"from missing import helper\n"),
])
def test_new_dependency_or_unresolved_topology_refuses_old_fresh_semantics(tmp_path, consumer, added):
    (tmp_path / "consumer.py").write_bytes(consumer)
    semantic = understand_project(tmp_path, project_scope="topology", identity=IDENTITY,
                                  analyzer=_analyze, budget=SemanticBudget(1), cache={})
    assert graph_operation(tmp_path, "tour", semantic=semantic)["tour"]["steps"][0]["narrative_status"] == "fresh"
    (tmp_path / "provider.py").write_bytes(added)
    with pytest.raises(GraphContractError, match="dependency topology is stale"):
        graph_operation(tmp_path, "tour", semantic=semantic)


def test_explicit_current_extra_dependency_remains_valid(tmp_path):
    for path in ("a.py", "b.py"):
        (tmp_path / path).write_bytes(b"VALUE = 1\n")
    semantic = understand_project(tmp_path, project_scope="topology", identity=IDENTITY,
                                  analyzer=_analyze, budget=SemanticBudget(2), cache={},
                                  dependencies={"a.py": ["b.py"]})
    assert graph_operation(tmp_path, "tour", semantic=semantic)["tour"]["steps"][0]["narrative_status"] == "fresh"
