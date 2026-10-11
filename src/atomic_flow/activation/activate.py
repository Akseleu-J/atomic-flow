"""BM25-style lexical activation with graceful no-model operation."""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from atomic_flow.tree.node import TreeNode

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|[^\W\d_]+", re.UNICODE)


def terms(text: str) -> list[str]:
    return [item.lower() for item in TOKEN_RE.findall(text) if len(item) > 1]


def activate(query: str, nodes: list[TreeNode], *, extra_terms: list[str] | None = None, limit: int = 40) -> list[tuple[TreeNode, float]]:
    query_terms = terms(query + " " + " ".join(extra_terms or []))
    if not query_terms:
        return [(node, 0.0) for node in nodes[:limit]]
    docs = [terms(" ".join((node.path, node.symbol, node.kind, node.signature, node.summary, " ".join(node.imports), " ".join(node.calls)))) for node in nodes]
    avg_len = sum(map(len, docs)) / max(1, len(docs))
    df = Counter(term for term in set(query_terms) for doc in docs if term in doc)
    scores: list[tuple[TreeNode, float]] = []
    for node, doc in zip(nodes, docs):
        freq = Counter(doc)
        score = 0.0
        for term in query_terms:
            f = freq[term]
            if not f:
                continue
            idf = math.log(1 + (len(docs) - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * (f * 2.2) / (f + 1.2 * (0.25 + 0.75 * len(doc) / max(avg_len, 1)))
        # Small structural priors favor executable symbols and exact path/name matches.
        if node.kind in {"function", "class"}:
            score *= 1.15
        if any(t == node.symbol.lower().split(".")[-1] or t in node.path.lower() for t in query_terms):
            score *= 1.35
        scores.append((node, score))
    scores.sort(key=lambda pair: (-pair[1], pair[0].path, pair[0].symbol))
    maximum = max((score for _, score in scores), default=0)
    return [(node, score / maximum if maximum else 0.0) for node, score in scores[:limit]]


def zoom_for(score: float) -> int:
    if score > 0.85:
        return 3
    if score >= 0.70:
        return 2
    if score >= 0.50:
        return 1
    return 0


def _source_for(root: Path, node: TreeNode, zoom: int) -> str:
    path = root / node.path
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ""
    if node.kind == "file":
        line_limit = {0: 24, 1: 60, 2: 120, 3: 240}[zoom]
        return "\n".join(lines[:line_limit])
    if node.kind in {"function", "class"}:
        if zoom == 0:
            return node.signature
        start, end = node.start_line, node.end_line
        if zoom == 1:
            end = min(end, start + 12)
        return "\n".join(lines[start - 1:end])
    if node.kind == "module":
        return "\n".join(lines[:12])
    return ""


def materialize(root: str | Path, candidates: list[tuple[TreeNode, float]], *, budget_tokens: int = 8000, include_skeleton: bool = True, selected_ids: set[str] | None = None, skeleton_nodes: list[tuple[TreeNode, float]] | None = None) -> tuple[str, list[dict]]:
    root_path = Path(root).expanduser().resolve()
    if selected_ids is not None:
        candidates = [(node, score) for node, score in candidates if node.node_id in selected_ids]
    skeleton_lines: list[str] = []
    if include_skeleton:
        for node, _ in (skeleton_nodes or candidates):
            if node.kind == "file":
                skeleton_lines.append(f"{node.path}")
            elif node.kind in {"class", "function"}:
                indent = node.symbol.count(".") + 1
                skeleton_lines.append(f"{'  ' * indent}{node.kind} {node.symbol} ({node.start_line}-{node.end_line})")
    skeleton_budget = max(128, budget_tokens // 2)
    while len(skeleton_lines) > 1 and len("\n".join(skeleton_lines)) // 4 > skeleton_budget:
        skeleton_lines.pop()
    skeleton = "PROJECT SKELETON\n" + "\n".join(skeleton_lines)
    sections = [skeleton] if include_skeleton else []
    metadata: list[dict] = []
    used = max(1, len(skeleton) // 4)
    for node, score in candidates:
        if node.kind == "file" and score < 0.2:
            continue
        zoom = zoom_for(score)
        body = _source_for(root_path, node, zoom)
        if not body.strip():
            body = node.signature or node.summary or (f"imports: {', '.join(node.imports)}" if node.imports else "")
        if not body.strip():
            continue
        block = f"\nNODE {node.node_id}\n{node.label} [{node.kind}, lines {node.start_line}-{node.end_line}, zoom={zoom}, score={score:.2f}]\n{body}"
        size = max(1, len(block) // 4)
        if used + size > budget_tokens:
            if not metadata:
                block = block[: max(0, (budget_tokens - used) * 4)]
                if len(block.strip()) < 40:
                    break
            else:
                break
        sections.append(block)
        used += size
        metadata.append({"node_id": node.node_id, "path": node.path, "symbol": node.symbol, "score": round(score, 4), "zoom": zoom})
    return "\n".join(sections), metadata
