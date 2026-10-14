"""Personalized PageRank over the call/reference graph."""
from __future__ import annotations

from collections import defaultdict


def build_adjacency(nodes) -> dict[str, set[str]]:
    """Build an undirected graph from call and reference edges."""
    adjacency: dict[str, set[str]] = defaultdict(set)
    for node in nodes:
        adjacency.setdefault(node.node_id, set())
        for callee in node.callees or []:
            adjacency[node.node_id].add(callee)
            adjacency[callee].add(node.node_id)
        for reference in getattr(node, "reference_ids", []) or []:
            adjacency[node.node_id].add(reference)
            adjacency[reference].add(node.node_id)
    return {node_id: neighbours for node_id, neighbours in adjacency.items()}


def pagerank(adj: dict[str, set[str]], seeds: dict[str, float], *, alpha: float = 0.85,
             max_iters: int = 20, tol: float = 1e-6) -> dict[str, float]:
    """Run personalized PageRank from positive seed weights."""
    nodes = [node_id for node_id in adj if adj[node_id] or node_id in seeds]
    if not nodes:
        return {}

    total = sum(weight for weight in seeds.values() if weight > 0)
    if total <= 0:
        personalization = {node_id: 1.0 / len(nodes) for node_id in nodes}
    else:
        personalization = {node_id: max(seeds.get(node_id, 0.0), 0.0) / total for node_id in nodes}

    scores = dict(personalization)
    for _ in range(max_iters):
        updated: dict[str, float] = {}
        for node_id in nodes:
            incoming = 0.0
            for neighbour in adj.get(node_id, ()):
                degree = len(adj.get(neighbour, ()))
                if degree > 0:
                    incoming += scores.get(neighbour, 0.0) / degree
            updated[node_id] = (1.0 - alpha) * personalization[node_id] + alpha * incoming

        total_score = sum(updated.values())
        if total_score > 0:
            updated = {node_id: value / total_score for node_id, value in updated.items()}
        difference = sum(abs(updated[node_id] - scores.get(node_id, 0.0)) for node_id in nodes)
        scores = updated
        if difference < tol:
            break
    return scores
