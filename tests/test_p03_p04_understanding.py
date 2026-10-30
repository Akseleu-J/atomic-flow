"""Understand host contracts and evidence-aware editing; scripted providers only."""
import io
import json

import pytest

from atomic_flow.backends.base import ChatResult
from atomic_flow.config import AtomicConfig, BackendConfig
from atomic_flow.graph.enrichment import SemanticBudget, enrich
from atomic_flow.graph.impact import verification_plan
from atomic_flow.graph.operations import graph_operation
from atomic_flow.graph.provider import SemanticProvider
from atomic_flow.graph.schema import GraphContractError
from atomic_flow.graph.semantic import semantic_hashes
from atomic_flow.llm.verify import citation_span, grounding_score, verification_report

IDENTITY = dict(extractor="ast", prompt="frozen", model="fixture", config="fixed", language="en")
FILES = {"a.py": b"from b import helper\ndef a():\n    return helper()\n",
         "b.py": b"def helper():\n    return 1\n"}


def analyze(request):
    return {"summary": "Explanation of " + request["path"], "tags": ["fixture"], "evidence": [{
        "node_id": "file:" + request["path"], "start_line": 1, "end_line": 1,
        "quote": request["source"].splitlines(keepends=True)[0]}]}


def run(files, cache, **kwargs):
    return enrich(files, project_scope="scope", identity=IDENTITY, analyzer=analyze,
                  budget=SemanticBudget(20), cache=cache, **kwargs)


def test_resolved_dependency_change_invalidates_consumer_and_narrative_fragments():
    cache = {}
    first = run(FILES, cache)
    assert run(FILES, cache)["calls"] == 0
    changed = {**FILES, "b.py": b"def helper():\n    return 2\n"}
    second = run(changed, cache)
    assert second["calls"] == 2
    assert first["artifacts"]["a.py"]["cache_key"] != second["artifacts"]["a.py"]["cache_key"]
    assert second["structural"]["flows"]
    assert all(step["source_digest"] and step["evidence"] for step in second["structural"]["tour"]["steps"])
    assert second["review"]["surviving_symbols"] == ["sym:a.py::a", "sym:b.py::helper"]


def test_hashes_distinguish_body_api_docs_and_comments():
    original = semantic_hashes(b'def f(x):\n    """old docs"""\n    return x\n')
    docs = semantic_hashes(b'def f(x):\n    """new docs"""\n    return x\n')
    assert docs["body"] == original["body"] and docs["docs"] != original["docs"]
    body = semantic_hashes(b'def f(x):\n    """old docs"""\n    return x + 1\n')
    assert body["body"] != original["body"] and body["api"] == original["api"]
    comment = semantic_hashes(b'# a comment\ndef f(x):\n    """old docs"""\n    return x\n')
    assert comment["bytes"] != original["bytes"] and comment["body"] == original["body"]


def test_semantic_provider_reuses_existing_physical_budget_and_keeps_malformed_cost(tmp_path):
    class Backend:
        name = "scripted-understanding"
        config = BackendConfig(model="fixture", price_in_per_mtok=1, price_out_per_mtok=1, max_output_tokens=32)
        calls = []

        def chat(self, *args, **kwargs):
            self.calls.append(1)
            return ChatResult("not json", 1, 1, 0.0001)

    backend = Backend()
    provider = SemanticProvider(tmp_path, backend, AtomicConfig())
    with pytest.raises(GraphContractError, match="no automatic replay"):
        provider({"source": "x"})
    assert len(backend.calls) == 1 and provider.task.budget_spent == 0.0001
    assert provider.task.budget_events[0]["status"] == "completed"


def test_six_operations_are_current_source_bound_and_report_unsupported(tmp_path):
    for path, source in FILES.items():
        (tmp_path / path).write_bytes(source)
    (tmp_path / "unsupported.ts").write_bytes(b"const x = 1;\n")
    for operation in ("inspect", "search", "explain", "chat", "impact", "tour"):
        result = graph_operation(tmp_path, operation, query="helper", paths=["b.py"])
        assert result["source_revision"] and result["read_proof"] is False
        assert result["omissions"] == [{"path": "unsupported.ts", "reason": "unsupported_adapter"}]
        assert result["semantic_correctness"] == "not_assessed"
    impact = graph_operation(tmp_path, "impact", paths=["b.py"])["verification_plan"]
    assert "a.py" in impact["affected_paths"] and impact["required_final_check"] == "full_pytest"


def test_cached_semantic_output_cannot_cross_root_or_survive_source_drift(tmp_path):
    from atomic_flow.sdk import understand_project
    (tmp_path / "b.py").write_bytes(FILES["b.py"])
    semantic = understand_project(tmp_path, project_scope="fixture", identity=IDENTITY, analyzer=analyze,
                                  budget=SemanticBudget(1), cache={})
    assert graph_operation(tmp_path, "tour", semantic=semantic)["tour"]["steps"][0]["narrative_status"] == "fresh"
    import copy
    fabricated = copy.deepcopy(semantic)
    fabricated["artifacts"]["b.py"]["result"]["evidence"][0]["quote"] = "invented source"
    with pytest.raises(GraphContractError, match="exact current source"):
        graph_operation(tmp_path, "tour", semantic=fabricated)
    (tmp_path / "b.py").write_bytes(b"def helper():\n    return 3\n")
    with pytest.raises(GraphContractError, match="stale"):
        graph_operation(tmp_path, "tour", semantic=semantic)
    semantic["scope"] = "somewhere-else::fixture"
    with pytest.raises(GraphContractError, match="scope"):
        graph_operation(tmp_path, "tour", semantic=semantic)


def test_unknown_impact_and_noncode_inputs_expand_checks_without_parsing_binary():
    result = verification_plan({"a.py": b"def f():\n    return unknown()\n", "asset.bin": b"\xff\0"}, ["a.py"])
    assert result["impact_status"] == "unknown" and result["loop_check"] == "full_pytest"
    assert result["omitted_unsupported"] == ["asset.bin"] and result["runtime_coverage"] == "not_assessed"


def test_empty_identifier_denominator_never_reports_success():
    assert grounding_score([], 0) is None
    report = verification_report([], 0)
    assert report["status"] == "not_assessed" and report["checked"] == 0 and report["score"] is None
    assert verification_report(["invented"], 1)["status"] == "failed"


def test_explicit_citation_lines_require_complete_current_source_and_exact_span():
    from atomic_flow.agents.source import digest
    from atomic_flow.llm.schemas import Citation
    from atomic_flow.tree.node import TreeNode
    source = b"VALUE = 1\ndef f():\n    return VALUE\n"
    node = TreeNode(node_id="a.py::f", path="a.py", symbol="f", kind="function", start_line=2, end_line=3)
    current = digest(source)
    valid = citation_span(Citation(node_id=node.node_id, lines="2-3"), node,
                          current_hash=current, shown_complete=True, source=source)
    assert valid["status"] == "passed" and valid["quote"] == "def f():\n    return VALUE\n"
    for lines, complete, raw in [("1-3", True, source), ("2-99", True, source), ("2-3", False, source),
                                  ("2-3", True, b"changed"), ("maybe2", True, source)]:
        assert citation_span(Citation(node_id=node.node_id, lines=lines), node,
                             current_hash=current, shown_complete=complete, source=raw)["status"] == "failed"


def test_cli_and_mcp_expose_graph_operations_without_constructing_provider(tmp_path, monkeypatch, capsys):
    from atomic_flow.cli import main
    from atomic_flow.integrations.mcp import PROTOCOL_VERSION, serve_mcp
    (tmp_path / "b.py").write_bytes(FILES["b.py"])
    monkeypatch.setattr("atomic_flow.cli.make_backend", lambda *a, **kw: pytest.fail("read-only graph started provider"))
    assert main(["graph", "chat", "--project", str(tmp_path), "--query", "helper"]) == 0
    assert json.loads(capsys.readouterr().out)["operation"] == "chat"
    messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL_VERSION}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "graph_tour", "arguments": {}}}]
    source = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in messages))
    output = io.BytesIO()
    serve_mcp(tmp_path, source, output)
    reply = json.loads(output.getvalue().splitlines()[-1])
    assert not reply["result"]["isError"]
    assert json.loads(reply["result"]["content"][0]["text"])["operation"] == "tour"
