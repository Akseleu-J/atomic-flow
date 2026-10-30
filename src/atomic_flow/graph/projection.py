"""Versioned structural projection for all host-admitted project source bytes.

The parser ceiling stays strict. Oversized source retains file identity and
explicit unknown extraction coverage, never a truncated AST or empty proof.
"""
from __future__ import annotations

import hashlib

from atomic_flow.graph.adapter_resource import adapt_source, resource_kind
from atomic_flow.graph.schema import GraphContractError, file_id

PROJECTION_POLICY = "project-file-projection/1"
PLACEHOLDER_EXTRACTOR = "graph-placeholder/1"
EXTRACTION_BYTE_LIMIT = 1_000_000
ADMITTED_BYTE_LIMIT = 16 * 1024**2


def project_file_projection(path, raw):
    owner = file_id(path)
    if not isinstance(raw, bytes) or len(raw) > ADMITTED_BYTE_LIMIT:
        raise GraphContractError("project projection requires host-admitted bounded source bytes")
    if not path.endswith((".py", ".pyi")) and resource_kind(path) is None:
        raise GraphContractError("unsupported source adapter: " + path)
    if len(raw) <= EXTRACTION_BYTE_LIMIT:
        return adapt_source(path, raw)
    facts = {"extractor": PLACEHOLDER_EXTRACTOR, "projection_policy": PROJECTION_POLICY,
             "extraction_status": "omitted_oversized", "omission_reason": "source_exceeds_extraction_byte_limit",
             "size_bytes": len(raw), "extraction_byte_limit": EXTRACTION_BYTE_LIMIT,
             "parse_ok": None, "imports": [], "raw_calls": [],
             "imports_status": "unknown", "calls_status": "unknown", "exports_status": "unknown"}
    node = {"id": owner, "kind": "file", "path": path,
            "source": {"digest": hashlib.sha256(raw).hexdigest(), "algorithm": "sha256"},
            "facts": facts, "fact_status": {name: "fresh" for name in facts}, "facts_class": "extracted"}
    return {"nodes": [node], "edges": []}


def coverage_from_file_nodes(nodes):
    """Reconstruct omissions solely from durable file facts, including reloads."""
    omissions = []
    for node in nodes:
        if node["kind"] != "file":
            continue
        facts = node["facts"]
        if facts.get("extraction_status") == "omitted_oversized":
            omissions.append({"path": node["path"], "reason": facts["omission_reason"],
                              "extractor": facts["extractor"], "policy": facts["projection_policy"],
                              "digest": node["source"]["digest"], "size_bytes": facts["size_bytes"]})
    return {"coverage_complete": not omissions,
            "coverage_omissions": sorted(omissions, key=lambda row: row["path"])}


def valid_file_extractor(node, expected):
    """Recognize only exact supported parser or placeholder policy versions."""
    facts = node["facts"]
    if facts.get("extractor") != PLACEHOLDER_EXTRACTOR:
        return facts.get("extractor") == expected and "extraction_status" not in facts
    return (node["source"].get("algorithm") == "sha256"
            and facts.get("projection_policy") == PROJECTION_POLICY
            and facts.get("extraction_status") == "omitted_oversized"
            and facts.get("omission_reason") == "source_exceeds_extraction_byte_limit"
            and type(facts.get("size_bytes")) is int
            and EXTRACTION_BYTE_LIMIT < facts["size_bytes"] <= ADMITTED_BYTE_LIMIT
            and facts.get("extraction_byte_limit") == EXTRACTION_BYTE_LIMIT
            and facts.get("parse_ok") is None and facts.get("imports") == [] and facts.get("raw_calls") == []
            and all(facts.get(key) == "unknown" for key in ("imports_status", "calls_status", "exports_status")))
