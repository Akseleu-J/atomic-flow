"""Path/symbol relocation is explicit; ambiguity never merges identities."""
import pytest

from atomic_flow.graph.delta import apply_changes
from atomic_flow.graph.project import clean_build
from atomic_flow.graph.schema import GraphContractError
from atomic_flow.graph.store import GraphStore


def seed(tmp_path, files):
    store = GraphStore(tmp_path / "graph.json")
    document = clean_build(files)
    store.commit(base_generation=0, put_nodes=document["nodes"], put_edges=document["edges"])
    return store


def test_unique_content_move_maps_file_and_surviving_qualified_symbols(tmp_path):
    raw = b"def value(): return 1\n"
    store = seed(tmp_path, {"old.py": raw})
    result = apply_changes(store, {"old.py": None, "new.py": raw})["relocation"]
    assert result["identity_mapping"] == {"file:old.py": "file:new.py", "sym:old.py::value": "sym:new.py::value"}
    assert result["path_moves"][0]["method"] == "unique_content_relocation"


def test_duplicate_content_does_not_guess_rename_identity(tmp_path):
    raw = b"def value(): return 1\n"
    store = seed(tmp_path, {"a.py": raw, "b.py": raw})
    result = apply_changes(store, {"a.py": None, "b.py": None, "c.py": raw, "d.py": raw})["relocation"]
    assert not result["path_moves"] and not result["identity_mapping"]
    assert result["ambiguous_moves"] and result["unmapped_removed"] == ["a.py", "b.py"]


def test_explicit_edited_move_maps_ids_and_bad_hint_cannot_commit(tmp_path):
    store = seed(tmp_path, {"old.py": b"def value(): return 1\n"})
    changed = {"old.py": None, "new.py": b"def value(): return 2\n"}
    before = store.path.read_bytes()
    with pytest.raises(GraphContractError, match="move mapping"):
        apply_changes(store, changed, moves={"wrong.py": "new.py"})
    assert store.path.read_bytes() == before
    result = apply_changes(store, changed, moves={"old.py": "new.py"})["relocation"]
    assert result["identity_mapping"]["sym:old.py::value"] == "sym:new.py::value"
    assert result["path_moves"][0]["method"] == "provided_hint"
