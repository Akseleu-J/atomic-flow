from conftest import ScriptedBackend

from atomic_flow.config import AtomicConfig
from atomic_flow.llm.orchestrator import Orchestrator
from atomic_flow.tree.builder import build_tree, save_tree


def test_new_source_is_used_for_grounding_and_critic(tmp_path):
    (tmp_path / "sample.py").write_text("def sample():\n    old_identifier = 1\n    return old_identifier\n", encoding="utf-8")
    save_tree(tmp_path, build_tree(tmp_path))
    backend = ScriptedBackend({
        "parser": {"task_type": "modify", "focus_terms": ["sample"]},
        "answerer": [
            {"tool": "edit_node", "args": {"node_id": "sample.py::sample",
                                           "new_source": "def sample():\n    new_identifier = 2\n    return new_identifier"}},
            {"text": "`sample` returns `new_identifier`.", "citations": [{"node_id": "sample.py::sample"}]},
        ],
        "critic": {"verdict": "ok"},
    })
    result = Orchestrator(AtomicConfig(), tmp_path, backend, approval_callback=lambda *a: True).answer(
        "update sample", mode="medium", approve_edits=True,
    )
    assert result.grounding == 1 and result.unknown_identifiers == []
    assert result.citations[0].node_id == "sample.py::sample"
    critic_prompt = next(messages[1]["content"] for role, messages in backend.calls if role == "critic")
    assert "new_identifier" in critic_prompt and "old_identifier" not in critic_prompt


def test_tool_read_outside_initial_context_is_valid_evidence(tmp_path):
    (tmp_path / "sample.py").write_text("def sample():\n    return 1\n", encoding="utf-8")
    (tmp_path / "extra.py").write_text("def obscure_helper():\n    special_marker = 42\n    return special_marker\n", encoding="utf-8")
    save_tree(tmp_path, build_tree(tmp_path))
    backend = ScriptedBackend({
        "answerer": [
            {"tool": "read_node", "args": {"node_id": "extra.py::obscure_helper", "zoom": 2}},
            {"text": "`obscure_helper` returns `special_marker`.", "citations": [{"node_id": "extra.py::obscure_helper"}]},
        ],
    })
    result = Orchestrator(AtomicConfig(), tmp_path, backend).answer("sample", mode="simple", budget_tokens=256)
    assert result.grounding == 1 and result.dropped_citations == 0
    assert result.citations[0].node_id == "extra.py::obscure_helper"
