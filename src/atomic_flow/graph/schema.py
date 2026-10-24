"""Versioned structural graph contract (roadmap P01).

Facts carry provenance and freshness; line numbers are revision coordinates
only. Identity is project path + qualified symbol; ambiguous matches are never
merged silently. This module defines and validates the schema; storage lives
in :mod:`atomic_flow.graph.store`.
"""
from __future__ import annotations

import re
from typing import Any

GRAPH_SCHEMA_VERSION = 1
NODE_KINDS = frozenset({"file", "module", "class", "function"})
EDGE_KINDS = frozenset({"contains", "imports", "calls", "references"})
FACT_CLASSES = frozenset({"extracted", "inferred", "runtime_observed"})
STATUS = frozenset({"fresh", "stale"})
EDGE_STATUS = frozenset({"active", "tombstone"})
_QUALIFIED = re.compile(r"^[A-Za-z_][\w.]*$")


class GraphContractError(ValueError):
    pass


def file_id(path: str) -> str:
    return "file:" + _posix(path)


def symbol_id(path: str, qualified: str, disambiguator: int = 1) -> str:
    suffix = "" if disambiguator == 1 else f"#{disambiguator}"
    return f"sym:{_posix(path)}::{qualified}{suffix}"


def _posix(path: str) -> str:
    return path.replace("\\", "/")


def validate_node(node: dict[str, Any]) -> None:
    if not isinstance(node, dict):
        raise GraphContractError("node must be an object")
    required = {"id", "kind", "path", "source", "facts", "fact_status"}
    missing = required - set(node)
    if missing:
        raise GraphContractError(f"node missing fields: {sorted(missing)}")
    if node["kind"] not in NODE_KINDS:
        raise GraphContractError(f"unsupported node kind {node['kind']!r}")
    if not node["path"] or "\\" in node["path"]:
        raise GraphContractError("node path must be non-empty posix-relative")
    source = node["source"]
    if not isinstance(source, dict) or not {"digest", "algorithm"} <= set(source):
        raise GraphContractError("node.source needs digest+algorithm")
    if node["kind"] == "file":
        if node["id"] != file_id(node["path"]):
            raise GraphContractError("file id mismatch")
    else:
        if not node.get("symbol") or not _QUALIFIED.match(node["symbol"]):
            raise GraphContractError("symbol nodes need a qualified symbol")
        prefix = f"sym:{node['path']}::{node['symbol']}"
        if node["id"] != prefix and not (node["id"].startswith(prefix + "#")
                                         and node["id"][len(prefix) + 1:].isdigit()):
            raise GraphContractError("symbol id must derive from path+symbol")
        if "start_line" not in node or "end_line" not in node:
            raise GraphContractError("symbol nodes carry revision line coordinates")
    if not isinstance(node["facts"], dict) or not isinstance(node["fact_status"], dict):
        raise GraphContractError("facts/fact_status must be objects")
    unknown_status = set(node["fact_status"].values()) - STATUS
    if unknown_status:
        raise GraphContractError(f"unknown fact status {sorted(unknown_status)}")
    for name in node["fact_status"]:
        if name not in node["facts"]:
            raise GraphContractError(f"status for unknown fact {name!r}")
    if node.get("facts_class", "extracted") not in FACT_CLASSES:
        raise GraphContractError("unknown facts class")


def validate_edge(edge: dict[str, Any]) -> None:
    if not isinstance(edge, dict) or not {"id", "kind", "src", "dst", "status"} <= set(edge):
        raise GraphContractError("edge missing fields")
    if edge["kind"] not in EDGE_KINDS:
        raise GraphContractError(f"unsupported edge kind {edge['kind']!r}")
    if edge["status"] not in EDGE_STATUS:
        raise GraphContractError(f"unknown edge status {edge['status']!r}")
    if edge["src"] == edge["dst"]:
        raise GraphContractError("self edge refused")
    expected = f"{edge['src']}-{edge['kind']}->{edge['dst']}"
    if edge["id"] != expected:
        raise GraphContractError("edge id must be derived from src/kind/dst")


def edge_id(src: str, kind: str, dst: str) -> str:
    return f"{src}-{kind}->{dst}"
