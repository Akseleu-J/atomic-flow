"""PageRank on the call graph surfaces a function's cluster."""
from __future__ import annotations

import pytest

from atomic_flow.tree.pagerank import build_adjacency, pagerank


class FakeNode:
    def __init__(self, node_id, callees=None, reference_ids=None):
        self.node_id = node_id
        self.callees = callees or []
        self.reference_ids = reference_ids or []


def test_pagerank_spreads_from_seed():
    nodes = [
        FakeNode("a", callees=["b"]),
        FakeNode("b", callees=["c"]),
        FakeNode("c"),
        FakeNode("d", callees=["b"]),
    ]
    pr = pagerank(build_adjacency(nodes), {"a": 1.0})
    assert pr["a"] > 0
    assert pr["b"] > pr["c"]
    assert pr["d"] > 0
    assert sum(pr.values()) == pytest.approx(1.0, abs=1e-6)


def test_pagerank_with_references():
    nodes = [FakeNode("core", reference_ids=["fwd", "bwd"]), FakeNode("fwd"), FakeNode("bwd")]
    pr = pagerank(build_adjacency(nodes), {"core": 1.0})
    assert pr["fwd"] > 0
    assert pr["bwd"] > 0
    assert pr["fwd"] == pytest.approx(pr["bwd"], rel=1e-3)


def test_pagerank_empty_graph():
    assert pagerank({}, {}) == {}


def test_pagerank_single_node():
    pr = pagerank(build_adjacency([FakeNode("only")]), {"only": 1.0})
    assert pr["only"] == pytest.approx(1.0, abs=1e-6)
