from atomic_flow.activation.activate import ActivationIndex, materialize
from atomic_flow.tree.builder import load_tree
from atomic_flow.tree.node import TreeNode


def load(project):
    return [TreeNode.model_validate(n) for n in load_tree(project)["nodes"]]


def test_refundable_matches_is_refundable(project):
    index = ActivationIndex(load(project))
    result = index.search("What exact rule makes an order refundable?")
    assert result.ranked[0][0].node_id == "invoice_service.py::is_refundable"


def test_stopwords_do_not_boost_paths(project):
    index = ActivationIndex(load(project))
    result = index.search("is or an to in")
    assert result.weak and result.top_raw == 0.0


def test_graph_spreading_pulls_callee(project):
    index = ActivationIndex(load(project))
    ids = [n.node_id for n, _ in index.search("How does refund_message decide?").ranked[:3]]
    assert "invoice_service.py::is_refundable" in ids


def test_materialize_skips_children_and_numbers_lines(project):
    nodes = load(project)
    by_id = {n.node_id: n for n in nodes}
    cls, method = by_id["invoice_service.py::Invoice"], by_id["invoice_service.py::Invoice.total"]
    context, meta = materialize(project, [(cls, 1.0), (method, 0.9)], budget_tokens=4000, skeleton_nodes=nodes,
                                aliases={n.node_id: f"n{i}" for i, n in enumerate(nodes, 1)})
    assert [m["symbol"] for m in meta] == ["Invoice"]                 # method already inside the class body
    assert "|     def subtotal" in context


def test_oversized_node_degrades_instead_of_ending_loop(project):
    nodes = load(project)
    by_id = {n.node_id: n for n in nodes}
    big, small = by_id["invoice_service.py::Invoice"], by_id["invoice_service.py::is_refundable"]
    _, meta = materialize(project, [(big, 1.0), (small, 0.9)], budget_tokens=200, include_skeleton=False)
    assert {m["symbol"]: m["zoom"] for m in meta} == {"Invoice": 0, "is_refundable": 3}   # class degraded, nothing blocked


def test_stale_file_is_skipped(project):
    nodes = load(project)
    node = next(n for n in nodes if n.node_id == "invoice_service.py::is_refundable")
    (project / "invoice_service.py").write_text("# edited after indexing\n" + (project / "invoice_service.py").read_text())
    context, meta = materialize(project, [(node, 1.0)], budget_tokens=2000, include_skeleton=False, skeleton_nodes=nodes)
    assert meta == []                                                  # never serve mis-sliced line ranges
