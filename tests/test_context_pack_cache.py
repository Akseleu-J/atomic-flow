"""Incremental advisory context packs retain freshness without AST-wide rebuilds."""
import pytest

from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.graph import impact
from atomic_flow.project_resources import ResourceLimitError

FILES = {
    "app.py": b"from lib import helper\n\ndef run():\n    return helper()\n",
    "lib.py": b"def helper():\n    return 1\n",
    "noise.py": b"VALUE = 1\n",
}


def canonical(pack):
    return {key: value for key, value in pack.items() if key != "stats"}


def counted_adapter(monkeypatch):
    calls = []
    original = impact.adapt_file

    def adapt(path, source):
        result = original(path, source)
        calls.append(path)
        return result

    monkeypatch.setattr(impact, "adapt_file", adapt)
    return calls


def test_warm_identical_snapshot_reuses_every_file_without_ast_parse(monkeypatch):
    calls = counted_adapter(monkeypatch)
    index = impact.ContextPackIndex()
    cold = impact.context_pack(FILES, ["app.py"], index=index)
    assert sorted(calls) == sorted(FILES)
    calls.clear()
    warm = impact.context_pack(dict(FILES), ["app.py"], index=index)
    assert not calls
    assert warm["stats"]["parsed_files"] == 0
    assert warm["stats"]["reused_files"] == len(FILES)
    assert warm["stats"]["deleted_files"] == 0
    assert warm["stats"]["scanned_files"] == len(FILES)
    assert canonical(warm) == canonical(cold)
    assert len(warm["revision"]) == 64


def test_same_size_body_edit_reparses_only_changed_file_and_rollback_restores_revision(monkeypatch):
    calls = counted_adapter(monkeypatch)
    index = impact.ContextPackIndex()
    before = impact.context_pack(FILES, ["lib.py"], index=index)
    changed = {**FILES, "lib.py": FILES["lib.py"].replace(b"return 1", b"return 2")}
    assert len(changed["lib.py"]) == len(FILES["lib.py"])
    calls.clear()
    after = impact.context_pack(changed, ["lib.py"], index=index)
    assert calls == ["lib.py"]
    assert after["stats"]["parsed_files"] == 1
    assert after["stats"]["reused_files"] == len(FILES) - 1
    assert after["revision"] != before["revision"]
    calls.clear()
    restored = impact.context_pack(FILES, ["lib.py"], index=index)
    assert calls == ["lib.py"]
    assert canonical(restored) == canonical(before)


@pytest.mark.parametrize("changed", [
    {**FILES, "lib.py": b"def replacement():\n    return 3\n"},
    {**FILES, "new.py": b"def new_helper():\n    return 1\n"},
    {path: source for path, source in FILES.items() if path != "lib.py"},
    {**{path: source for path, source in FILES.items() if path != "noise.py"},
     "misc/noise.py": FILES["noise.py"]},
])
def test_delta_add_delete_move_and_symbol_changes_match_fresh_pack(changed):
    index = impact.ContextPackIndex()
    impact.context_pack(FILES, ["app.py"], index=index)
    delta = impact.context_pack(changed, ["app.py"], index=index)
    fresh = impact.context_pack(changed, ["app.py"])
    assert canonical(delta) == canonical(fresh)
    assert delta["stats"]["parsed_files"] == sum(FILES.get(path) != source for path, source in changed.items())
    assert delta["stats"]["deleted_files"] == len(set(FILES) - set(changed))


def test_goal_pack_includes_outgoing_dependency_as_advisory_evidence():
    pack = impact.context_pack(FILES, ["app.py"])
    assert pack["entries"][0]["file"] == "app.py"
    dependency = next(entry for entry in pack["entries"] if entry["file"] == "lib.py")
    assert dependency["reason"] != "goal file"
    assert dependency["reason"]
    assert not any(entry["file"] == "noise.py" for entry in pack["entries"])
    assert "advisory" in pack["method"]


def test_cancelled_multi_file_update_never_leaves_partially_new_snapshot(monkeypatch):
    index = impact.ContextPackIndex()
    before = impact.context_pack(FILES, ["app.py"], index=index)
    calls = counted_adapter(monkeypatch)
    changed = {**FILES, "app.py": FILES["app.py"] + b"\nA = 2\n",
               "lib.py": FILES["lib.py"].replace(b"return 1", b"return 2")}

    def cancel_after_first_parse():
        if calls:
            raise TaskCancelled("cancel between changed files")

    with pytest.raises(TaskCancelled, match="cancel between"):
        impact.context_pack(changed, ["app.py"], index=index, cancel_check=cancel_after_first_parse)
    assert calls  # The refusal actually happened after some candidate work.
    calls.clear()
    unchanged = impact.context_pack(FILES, ["app.py"], index=index)
    assert not calls
    assert unchanged["stats"]["parsed_files"] == 0
    assert canonical(unchanged) == canonical(before)


def test_node_limit_refusal_retains_previous_complete_snapshot(monkeypatch):
    index = impact.ContextPackIndex()
    before = impact.context_pack(FILES, ["app.py"], index=index)
    changed = {**FILES, "extra.py": b"def first(): pass\n\ndef second(): pass\n"}
    with pytest.raises((ResourceLimitError, ValueError), match="node"):
        impact.context_pack(changed, ["app.py"], index=index, max_nodes=1)
    calls = counted_adapter(monkeypatch)
    unchanged = impact.context_pack(FILES, ["app.py"], index=index)
    assert not calls
    assert canonical(unchanged) == canonical(before)


def test_node_limit_allows_growth_offset_by_later_file_shrink():
    files = {"a.py": b"A = 1\n",
             "z.py": b"def first(): pass\n\ndef second(): pass\n\ndef third(): pass\n"}
    index = impact.ContextPackIndex()
    # Two file nodes plus three function nodes: five nodes in both revisions.
    impact.context_pack(files, ["a.py"], index=index, max_nodes=5)
    changed = {"a.py": b"def first(): pass\n\ndef second(): pass\n\ndef third(): pass\n",
               "z.py": b"Z = 1\n"}
    # a.py sorts first, but temporary counting must subtract z.py's obsolete
    # symbols before deciding that the final replacement exceeds admission.
    updated = impact.context_pack(changed, ["a.py"], index=index, max_nodes=5)
    fresh = impact.context_pack(changed, ["a.py"], max_nodes=5)
    assert canonical(updated) == canonical(fresh)
    assert updated["stats"]["parsed_files"] == 2
