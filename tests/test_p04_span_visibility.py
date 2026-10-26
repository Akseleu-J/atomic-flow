"""A complete node representation is not a complete paginated source read."""
import json
from pathlib import Path

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


def test_host_span_failure_reaches_retry_and_saved_critique(tmp_path):
    (tmp_path / "visible.py").write_text("def visible():\n    return 1\n", encoding="utf-8")
    hidden = "def secret():\n" + "".join(f"    value_{i} = {i}\n" for i in range(200)) + "    return value_199\n"
    (tmp_path / "hidden.py").write_text(hidden, encoding="utf-8")
    save_tree(tmp_path, build_tree(tmp_path))

    def repair(user):
        assert '"verdict": "needs_retry"' in user
        assert "Host rejected citation spans" in user
        assert "unavailable or stale complete source span" in user
        return {"tool": "read_node", "args": {"node_id": "hidden.py::secret", "zoom": 2, "limit": 6000}}

    answer = {"text": "The last line returns a value.",
              "citations": [{"node_id": "hidden.py::secret", "lines": "202"}]}
    backend = ScriptedBackend({"parser": {}, "answerer": [
        {"tool": "read_node", "args": {"node_id": "hidden.py::secret", "zoom": 2, "limit": 256}},
        answer, repair, answer], "critic": {"verdict": "ok", "citation_coverage": 1}})
    result = Orchestrator(AtomicConfig(), tmp_path, backend).answer("Explain visible.py", mode="medium", budget_tokens=256)
    assert result.verdict == "ok" and result.attempts == 2
    assert result.verification_report["citation_spans"]["status"] == "passed"
    saved = json.loads((Path(result.artifacts_path) / "attempt_1_critique.json").read_text())
    assert saved["verdict"] == "needs_retry" and "Host rejected citation spans" in saved["feedback"]
