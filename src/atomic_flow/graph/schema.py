"""Versioned structural graph contract (roadmap P01).

Facts carry provenance and freshness; line numbers are revision coordinates
only. Identity is project path + qualified symbol; ambiguous matches are never
merged silently. This module defines and validates the schema; storage lives
in :mod:`atomic_flow.graph.store`.
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any

GRAPH_SCHEMA_VERSION = 1
NODE_KINDS = frozenset({"file", "module", "class", "function", "config", "document", "pipeline"})
EDGE_KINDS = frozenset({"contains", "imports", "calls", "references"})
FACT_CLASSES = frozenset({"extracted", "inferred", "runtime_observed"})
STATUS = frozenset({"fresh", "stale"})
EDGE_STATUS = frozenset({"active", "tombstone"})
_HEX = re.compile(r"^[a-f0-9]+$")


class GraphContractError(ValueError):
    pass


def file_id(path: str) -> str:
    return "file:" + _posix(path)


def symbol_id(path: str, qualified: str, disambiguator: int = 1) -> str:
    if (not isinstance(qualified, str) or not qualified
            or not all(p.isidentifier() for p in qualified.split("."))
            or type(disambiguator) is not int or disambiguator < 1):
        raise GraphContractError("invalid qualified symbol/disambiguator")
    suffix = "" if disambiguator == 1 else f"#{disambiguator}"
    return f"sym:{_posix(path)}::{qualified}{suffix}"


def _posix(path: str) -> str:
    if (not isinstance(path, str) or not path or len(path.encode("utf-8")) > 4096
            or any(c in path for c in "\\:\x00\r\n") or PurePosixPath(path).is_absolute()
            or PurePosixPath(path).as_posix() != path
            or any(p in {".", ".."} for p in path.split("/"))):
        raise GraphContractError("graph path must be canonical project-relative POSIX")
    return path


def _facts(value: dict) -> None:
    facts, statuses = value.get("facts"), value.get("fact_status")
    if not isinstance(facts, dict) or not isinstance(statuses, dict) or set(statuses) != set(facts):
        raise GraphContractError("every fact requires an explicit freshness status")
    if any(not isinstance(k, str) for k in facts) or any(not isinstance(s, str) or s not in STATUS for s in statuses.values()):
        raise GraphContractError("invalid fact name/freshness")
    if not isinstance(value.get("facts_class", "extracted"), str) or value.get("facts_class", "extracted") not in FACT_CLASSES:
        raise GraphContractError("unknown facts class")
    try:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise GraphContractError("graph facts must be finite JSON data") from exc
    if len(data.encode("utf-8")) > 2_000_000:
        raise GraphContractError("graph record exceeds byte bound")


def validate_node(node: dict[str, Any]) -> None:
    if not isinstance(node, dict):
        raise GraphContractError("node must be an object")
    required = {"id", "kind", "path", "source", "facts", "fact_status"}
    missing = required - set(node)
    if missing:
        raise GraphContractError(f"node missing fields: {sorted(missing)}")
    if set(node) - (required | {"symbol", "start_line", "end_line", "facts_class"}):
        raise GraphContractError("unknown node wire fields; put versioned metadata in facts")
    if not isinstance(node["kind"], str) or node["kind"] not in NODE_KINDS or not isinstance(node["id"], str):
        raise GraphContractError(f"unsupported node kind {node['kind']!r}")
    _posix(node["path"])
    source = node["source"]
    if not isinstance(source, dict) or set(source) != {"digest", "algorithm"}:
        raise GraphContractError("node.source needs digest+algorithm")
    algorithm, digest = source["algorithm"], source["digest"]
    if not isinstance(algorithm, str) or not isinstance(digest, str) or len(digest) > 128:
        raise GraphContractError("invalid source digest")
    lengths = {"sha256": 64, "sha1": 40}
    if algorithm in lengths:
        if len(digest) != lengths[algorithm] or not _HEX.fullmatch(digest):
            raise GraphContractError("source digest does not match its algorithm")
    elif algorithm not in {"legacy", "legacy-file"}:
        raise GraphContractError("unsupported source digest algorithm")
    if node["kind"] == "file":
        if node["id"] != file_id(node["path"]):
            raise GraphContractError("file id mismatch")
    else:
        if (not isinstance(node.get("symbol"), str) or not node["symbol"]
                or not all(part.isidentifier() for part in node["symbol"].split("."))):
            raise GraphContractError("symbol nodes need a qualified symbol")
        prefix = f"sym:{node['path']}::{node['symbol']}"
        if node["id"] != prefix and not (node["id"].startswith(prefix + "#")
                                         and node["id"][len(prefix) + 1:].isdigit()
                                         and int(node["id"][len(prefix) + 1:]) >= 2):
            raise GraphContractError("symbol id must derive from path+symbol")
        if "start_line" not in node or "end_line" not in node:
            raise GraphContractError("symbol nodes carry revision line coordinates")
        if (type(node["start_line"]) is not int or type(node["end_line"]) is not int
                or not 1 <= node["start_line"] <= node["end_line"]):
            raise GraphContractError("invalid revision line span")
    _facts(node)


def validate_edge(edge: dict[str, Any]) -> None:
    if not isinstance(edge, dict) or not {"id", "kind", "src", "dst", "status"} <= set(edge):
        raise GraphContractError("edge missing fields")
    if set(edge) - {"id", "kind", "src", "dst", "status", "facts", "fact_status", "facts_class"}:
        raise GraphContractError("unknown edge wire fields; put versioned metadata in facts")
    if any(not isinstance(edge[key], str) for key in ("id", "kind", "src", "dst", "status")):
        raise GraphContractError("edge identity/kind/status must be strings")
    if edge["kind"] not in EDGE_KINDS:
        raise GraphContractError(f"unsupported edge kind {edge['kind']!r}")
    if edge["status"] not in EDGE_STATUS:
        raise GraphContractError(f"unknown edge status {edge['status']!r}")
    if edge["src"] == edge["dst"]:
        raise GraphContractError("self edge refused")
    expected = f"{edge['src']}-{edge['kind']}->{edge['dst']}"
    if edge["id"] != expected:
        raise GraphContractError("edge id must be derived from src/kind/dst")
    _facts(edge)


def edge_id(src: str, kind: str, dst: str) -> str:
    return f"{src}-{kind}->{dst}"
