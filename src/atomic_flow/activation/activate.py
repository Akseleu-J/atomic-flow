"""BM25F-style lexical activation + call-graph spreading, and budgeted context materialization."""

from __future__ import annotations

import hashlib
import logging
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from atomic_flow.activation.redact import redact
from atomic_flow.activation.text import raw_terms, synonyms_for, terms
from atomic_flow.textutil import estimate_tokens, split_lines
from atomic_flow.tree.node import TreeNode
from atomic_flow.tree.pagerank import build_adjacency, pagerank

log = logging.getLogger(__name__)

K1, B = 1.2, 0.75
GENERIC_NAMES = frozenset({"step", "body", "main", "run", "init", "test", "setup"})
FIELD_WEIGHTS = {"symbol": 3.0, "path": 1.5, "signature": 1.5, "summary": 1.0, "callees": 0.7, "imports": 0.4}
SPREAD_CALLEE, SPREAD_CALLER, SPREAD_PARENT = 0.55, 0.40, 0.35
SPREAD_REFERENCE = 0.30


def _kind_penalty(path: str) -> float:
    """Keep tests, archives, and benchmarks from outranking production code by accident."""
    p = path.lower()
    if p.startswith("tests/") or "/tests/" in p or p.startswith("test_") or "/test_" in p:
        return 0.25
    if "archive/" in p or "_backup_" in p:
        return 0.1
    if "benchmark" in p or "/benchmarks/" in p:
        return 0.4
    return 1.0


@dataclass
class SearchResult:
    ranked: list[tuple[TreeNode, float]]      # normalized 0..1, top lexical hit == 1.0, includes graph spreading
    top_raw: float = 0.0
    matched_ratio: float = 0.0                # share of query terms present in the top-10 nodes
    weak: bool = True                         # no hit, or most of the query is absent from the best nodes
    query_terms: list[str] = field(default_factory=list)


class ActivationIndex:
    """Inverted index built ONCE per tree (the old code re-tokenized every node on every query)."""

    def __init__(self, nodes: list[TreeNode]):
        self.nodes = nodes
        self.pos = {n.node_id: i for i, n in enumerate(nodes)}
        names = {n.node_id: n.symbol.rsplit(".", 1)[-1].split("#")[0] for n in nodes}
        self.tf: list[Counter[str]] = []
        self.dl: list[float] = []
        self.postings: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for i, n in enumerate(nodes):
            tf: Counter[str] = Counter()
            fields = {
                "symbol": n.symbol, "path": n.path, "signature": n.signature, "summary": n.summary,
                "callees": " ".join(names.get(c, "") for c in n.callees),
                "imports": " ".join(n.imports) if n.kind == "file" else "",
            }
            for name, text in fields.items():
                for term in terms(text):
                    tf[term] += FIELD_WEIGHTS[name]
            self.tf.append(tf)
            self.dl.append(sum(tf.values()))
            for term, weight in tf.items():
                self.postings[term].append((i, weight))
        self.avgdl = (sum(self.dl) / len(self.dl)) if self.dl else 1.0
        self.symbol_key = [names[n.node_id].lower() for n in nodes]
        self.file_stem = [Path(n.path).stem.lower() if n.kind == "file" else "" for n in nodes]
        self.kind_penalty = [_kind_penalty(n.path) for n in nodes]
        self.callers: dict[int, list[int]] = defaultdict(list)
        for i, n in enumerate(nodes):
            for callee in n.callees:
                if callee in self.pos:
                    self.callers[self.pos[callee]].append(i)
        self.adjacency = build_adjacency(nodes)

    def search(self, query: str, *, extra_terms: list[str] | None = None, limit: int = 40,
               scope: set[str] | None = None, seeds: int = 10) -> SearchResult:
        text = query + " " + " ".join(extra_terms or [])
        primary = list(dict.fromkeys(terms(text)))
        q_weight = {term: 1.0 for term in primary}
        for term in primary:
            for synonym in synonyms_for(term):
                q_weight.setdefault(synonym, 0.5)
        q_terms = list(q_weight)
        q_raw = raw_terms(text)
        n_docs = len(self.nodes)
        allowed = None if scope is None else {self.pos[s] for s in scope if s in self.pos}
        scores: dict[int, float] = defaultdict(float)
        for term, query_weight in q_weight.items():
            plist = self.postings.get(term)
            if not plist:
                continue
            df = len(plist)
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            for i, weight in plist:
                if allowed is not None and i not in allowed:
                    continue
                scores[i] += query_weight * idf * weight * (K1 + 1) / (weight + K1 * (1 - B + B * self.dl[i] / self.avgdl))
        for i in list(scores):
            kind = self.nodes[i].kind
            if kind in {"function", "class"}:
                scores[i] *= 1.15
            # Exact-name priors compare WHOLE tokens. (The old `t in node.path` was a substring test,
            # so stop-words like "is"/"or"/"an" boosted any path that merely contained those letters.)
            name = self.symbol_key[i]
            if (len(name) >= 4 and name not in GENERIC_NAMES and name in q_raw) or (self.file_stem[i] and self.file_stem[i] in q_raw):
                scores[i] *= 1.35
        for i in list(scores):
            scores[i] *= self.kind_penalty[i]
        order = sorted(scores.items(), key=lambda kv: (-kv[1], self.nodes[kv[0]].path, self.nodes[kv[0]].symbol))
        if not order or order[0][1] <= 0:
            hubs = sorted((i for i, n in enumerate(self.nodes)
                           if n.kind != "file" and (allowed is None or i in allowed)),
                          key=lambda i: (-self.nodes[i].fan_in, self.nodes[i].path, self.nodes[i].symbol))[:10]
            return SearchResult([(self.nodes[i], 0.1) for i in hubs], 0.0, 0.0, True, q_terms)
        top = order[0][1]
        norm = {i: s / top for i, s in order}
        seed_count = min(10, len(order))
        pagerank_seeds = {self.nodes[i].node_id: norm[i] for i, _ in order[:seed_count] if norm[i] > 0.2}
        if pagerank_seeds:
            graph_scores = pagerank(self.adjacency, pagerank_seeds, alpha=0.85, max_iters=15)
            if graph_scores:
                max_graph_score = max(graph_scores.values()) or 1.0
                for i in list(norm):
                    graph_norm = graph_scores.get(self.nodes[i].node_id, 0.0) / max_graph_score
                    norm[i] = 0.75 * norm[i] + 0.25 * graph_norm
        spread: dict[int, float] = {}
        for i, _ in order[:seeds]:
            a = norm[i]
            if a < 0.3:
                break
            node = self.nodes[i]
            related = [(self.pos[c], SPREAD_CALLEE) for c in node.callees if c in self.pos]
            related += [(self.pos[r], SPREAD_REFERENCE) for r in node.reference_ids if r in self.pos]
            related += [(j, SPREAD_CALLER) for j in self.callers.get(i, [])]
            if node.parent_id in self.pos and self.nodes[self.pos[node.parent_id]].kind == "class":
                related.append((self.pos[node.parent_id], SPREAD_PARENT))
            for j, w in related:
                if allowed is None or j in allowed:
                    spread[j] = max(spread.get(j, 0.0), a * w)
        final = dict(norm)
        for j, v in spread.items():
            final[j] = max(final.get(j, 0.0), v)
        ranked_idx = sorted(final.items(), key=lambda kv: (-kv[1], self.nodes[kv[0]].path, self.nodes[kv[0]].symbol))[:limit]
        top_terms: set[str] = set()
        for i, _ in order[:10]:
            top_terms.update(self.tf[i])
        matched = sum(1 for term in primary if term in top_terms)
        ratio = matched / len(primary) if primary else 0.0
        return SearchResult([(self.nodes[i], s) for i, s in ranked_idx], top, ratio, ratio < 0.2, q_terms)

    def neighbors(self, node_ids: set[str]) -> list[TreeNode]:
        """Callees/callers/parents of the given nodes that are not in the set (expansion candidates)."""
        out: dict[str, TreeNode] = {}
        for nid in node_ids:
            i = self.pos.get(nid)
            if i is None:
                continue
            node = self.nodes[i]
            for other in [*node.callees, *node.reference_ids,
                          *(self.nodes[j].node_id for j in self.callers.get(i, [])), node.parent_id or ""]:
                if other in self.pos and other not in node_ids and self.nodes[self.pos[other]].kind != "file":
                    out[other] = self.nodes[self.pos[other]]
        return sorted(out.values(), key=lambda n: (n.path, n.symbol))


def activate(query: str, nodes: list[TreeNode], *, extra_terms: list[str] | None = None, limit: int = 40):
    """Backwards-compatible helper. Prefer building one ``ActivationIndex`` and calling ``search``."""
    return ActivationIndex(nodes).search(query, extra_terms=extra_terms, limit=limit).ranked


def zoom_for(score: float) -> int:
    if score > 0.85:
        return 3
    if score >= 0.70:
        return 2
    if score >= 0.50:
        return 1
    return 0


# --------------------------------------------------------------------------- materialization

class _Sources:
    """Reads each file once per request and refuses files that changed after indexing."""

    def __init__(self, root: Path, hashes: dict[str, str]):
        self.root, self.hashes = root, hashes
        self.cache: dict[str, list[str] | None] = {}

    def lines(self, rel: str) -> list[str] | None:
        if rel not in self.cache:
            path = self.root / rel
            try:
                if not path.resolve().is_relative_to(self.root):
                    raise OSError("source is outside project root")
                data = path.read_bytes()
                text = data.decode("utf-8-sig")
            except (OSError, UnicodeError):
                self.cache[rel] = None
                return None
            expected = self.hashes.get(rel)
            if expected and hashlib.sha1(data).hexdigest() != expected:
                log.warning("Index is stale for %s; run `atomic tree refresh`. Skipping its source.", rel)
                self.cache[rel] = None
                return None
            self.cache[rel] = split_lines(redact(text))
        return self.cache[rel]


def _numbered(lines: list[str], first: int, numbers: bool) -> str:
    if not numbers:
        return "\n".join(lines)
    return "\n".join(f"{first + i:>4}| {line}" for i, line in enumerate(lines))


def build_skeleton(nodes: list[TreeNode], focus_paths: set[str], budget_tokens: int,
                   aliases: dict[str, str] | None = None) -> str:
    """Whole-project map. Files that matter for this query are expanded (with aliases the model can
    ask for); the rest are one line each and are dropped from the TAIL first when over budget."""
    aliases = aliases or {}
    by_file: dict[str, list[TreeNode]] = {}
    for n in nodes:
        by_file.setdefault(n.path, []).append(n)
    focus: list[str] = []
    other: list[str] = []
    for path, items in by_file.items():
        symbols = [n for n in items if n.kind != "file"]
        if path in focus_paths:
            focus.append(path)
            for n in symbols:
                tag = f" [{aliases[n.node_id]}]" if n.node_id in aliases else ""
                focus.append(f"{'  ' * (n.symbol.count('.') + 1)}{n.kind} {n.symbol} ({n.start_line}-{n.end_line}){tag}")
        else:
            other.append(f"{path} ({len(symbols)} symbols)" if symbols else path)
    lines = ["PROJECT SKELETON", *focus]
    used = estimate_tokens("\n".join(lines))
    shown = 0
    for line in other:
        cost = estimate_tokens(line) + 1
        if used + cost > budget_tokens:
            break
        lines.append(line)
        used += cost
        shown += 1
    if shown < len(other):
        lines.append(f"... +{len(other) - shown} more files not shown")
    return "\n".join(lines)


def _render(node: TreeNode, lines: list[str], zoom: int, numbers: bool) -> tuple[str, int]:
    """Returns (body, last_line_included)."""
    if node.kind == "file":
        limit = {0: 8, 1: 40, 2: 100, 3: 200}[zoom]
        body = lines[:limit]
        return _numbered(body, 1, numbers), len(body)
    if zoom == 0:
        return node.signature, node.start_line
    start = node.start_line
    end = node.end_line
    if zoom == 1:
        end = min(end, start + 13)
    elif zoom == 2:
        end = min(end, start + 79)
    body = lines[start - 1:end]
    text = _numbered(body, start, numbers)
    if end < node.end_line:
        text += f"\n      ... ({node.end_line - end} more lines)"
    return text, end


def materialize(root: str | Path, candidates: list[tuple[TreeNode, float]], *, budget_tokens: int = 8000,
                include_skeleton: bool = True, selected_ids: set[str] | None = None,
                skeleton_nodes: list[TreeNode] | None = None, aliases: dict[str, str] | None = None,
                min_zoom: dict[str, int] | None = None, line_numbers: bool = True) -> tuple[str, list[dict]]:
    """Render ranked nodes into a prompt context that fits ``budget_tokens``.

    Differences from v0.1: python ``file`` nodes are skipped (symbols already cover them); a child
    that lies inside an already-rendered parent is skipped; a node that does not fit DEGRADES to a
    lower zoom instead of ending the whole loop; the file index hash is checked (stale sources are
    skipped, not silently mis-sliced); every file is read once.
    """
    root_path = Path(root).expanduser().resolve()
    aliases = aliases or {}
    min_zoom = min_zoom or {}
    if selected_ids is not None:
        candidates = [(n, s) for n, s in candidates if n.node_id in selected_ids]
    hashes = {n.path: n.content_hash for n in (skeleton_nodes or []) if n.kind == "file" and n.content_hash}
    sources = _Sources(root_path, hashes)
    sections: list[str] = []
    used = 0
    if include_skeleton and skeleton_nodes:
        focus = {n.path for n, _ in candidates[:10]}
        skeleton = build_skeleton(skeleton_nodes, focus, max(128, budget_tokens // 2), aliases)
        sections.append(skeleton)
        used += estimate_tokens(skeleton)
    covered: dict[str, list[tuple[int, int]]] = defaultdict(list)
    meta: list[dict] = []
    for node, score in candidates:
        if node.kind == "file" and (node.path.endswith((".py", ".pyi")) or score < 0.2):
            continue
        lines = sources.lines(node.path)
        if lines is None:
            continue
        if node.kind != "file" and any(a <= node.start_line and node.end_line <= b for a, b in covered[node.path]):
            continue
        top_zoom = max(zoom_for(score), min_zoom.get(node.node_id, 0))
        placed = False
        for zoom in range(top_zoom, -1, -1):
            body, last = _render(node, lines, zoom, line_numbers)
            if not body.strip():
                body = node.signature or node.summary
            if not body.strip():
                break
            header = (f"\nNODE {aliases.get(node.node_id, node.node_id)}\n{node.label} "
                      f"[{node.kind}, lines {node.start_line}-{node.end_line}, zoom={zoom}, score={score:.2f}]\n")
            size = estimate_tokens(header + body)
            if used + size <= budget_tokens:
                sections.append(header + body)
                used += size
                if node.kind != "file":
                    covered[node.path].append((node.start_line, last))
                meta.append({"node_id": node.node_id, "alias": aliases.get(node.node_id), "path": node.path,
                             "symbol": node.symbol, "score": round(score, 4), "zoom": zoom, "tokens": size,
                             "rendered_chars": len(body), "content_hash": hashes.get(node.path, node.content_hash),
                             "source_complete": zoom >= 2 and last >= (len(lines) if node.kind == "file" else node.end_line)})
                placed = True
                break
        if not placed and used >= budget_tokens - 8:
            break
    return "\n".join(sections), meta
