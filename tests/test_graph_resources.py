"""Resource adapter parity, omission and inert configuration regressions."""
import pytest

from atomic_flow.graph.adapter_resource import adapt_source
from atomic_flow.graph.delta import apply_delta, file_delta
from atomic_flow.graph.project import clean_build
from atomic_flow.graph.schema import GraphContractError
from atomic_flow.graph.store import GraphStore


@pytest.mark.parametrize("path,kind", [("pyproject.toml", "config"), ("README.md", "document"),
                                      (".github/workflows/ci.yaml", "pipeline")])
def test_resource_roundtrip_and_delta_parity(tmp_path, path, kind):
    before = b"# source metadata\n"
    build = clean_build({path: before})
    assert [n["kind"] for n in build["nodes"]] == ["file", kind]
    store = GraphStore(tmp_path / "graph.json")
    store.commit(base_generation=0, put_nodes=build["nodes"], put_edges=build["edges"])
    apply_delta(store, file_delta(path, before + b"# changed\n", expected_old_digest=build["nodes"][0]["source"]["digest"]))
    expected = clean_build({path: before + b"# changed\n"})
    assert store.nodes() == sorted(expected["nodes"], key=lambda n: n["id"])
    assert store.edges() == sorted(expected["edges"], key=lambda e: e["id"])
    assert all(n["facts"].get("semantic_status") == "not_assessed" for n in store.nodes())


def test_yaml_is_inert_and_unsupported_adapter_explicit(tmp_path):
    target = tmp_path / "owned"
    raw = ("!!python/object/apply:os.system ['touch " + str(target) + "']").encode()
    adapted = adapt_source("danger.yaml", raw)
    assert adapted["nodes"][0]["facts"]["parse_ok"] is None
    assert not target.exists()
    assert adapt_source("broken.json", b"{")["nodes"][0]["facts"]["parse_ok"] is False
    with pytest.raises(GraphContractError, match="unsupported"):
        adapt_source("app.ts", b"function f() {}")
