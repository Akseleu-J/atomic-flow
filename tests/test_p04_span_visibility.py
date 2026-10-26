"""A complete node representation is not a complete paginated source read."""
from conftest import ScriptedBackend

from atomic_flow.config import AtomicConfig
from atomic_flow.llm.orchestrator import Orchestrator
from atomic_flow.tree.builder import build_tree, save_tree


def test_partial_node_page_cannot_verify_unshown_source_lines(tmp_path):
    (tmp_path / "visible.py").write_text("def visible():\n    return 1\n", encoding="utf-8")
    hidden = "def secret():\n" + "".join(f"    value_{i} = {i}\n" for i in range(200)) + "    return value_199\n"
    (tmp_path / "hidden.py").write_text(hidden, encoding="utf-8")
    save_tree(tmp_path, build_tree(tmp_path))
    backend = ScriptedBackend({"answerer": [
        {"tool": "read_node", "args": {"node_id": "hidden.py::secret", "zoom": 2, "limit": 256}},
        {"text": "The last line returns a value.", "citations": [{"node_id": "hidden.py::secret", "lines": "202"}]},
    ]})
    result = Orchestrator(AtomicConfig(), tmp_path, backend).answer("Explain visible.py", mode="simple", budget_tokens=256)
    assert not result.citations and result.dropped_citations == 1
    spans = result.verification_report["citation_spans"]
    assert spans["status"] == "failed" and spans["checked"] == 1
    assert spans["items"][0]["status"] == "failed"
