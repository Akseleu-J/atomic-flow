"""Source-only adapters for UA config/document/pipeline resources.

No YAML constructors, templates, workflow steps or repository code are executed.
Resource classification is path-based and does not imply semantic correctness.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath

from atomic_flow.graph.adapter_ast import adapt_file
from atomic_flow.graph.schema import GraphContractError, edge_id, file_id, symbol_id

RESOURCE_EXTRACTOR = "resource-metadata/1"


def resource_kind(path: str) -> str | None:
    suffix = PurePosixPath(path).suffix.lower()
    if path.startswith(".github/workflows/") and suffix in {".yaml", ".yml"}:
        return "pipeline"
    if suffix in {".toml", ".json", ".yaml", ".yml"}:
        return "config"
    if suffix == ".md":
        return "document"
    return None


def adapt_source(path: str, source: bytes) -> dict:
    if path.endswith((".py", ".pyi")):
        return adapt_file(path, source)
    kind = resource_kind(path)
    if kind is None:
        raise GraphContractError("unsupported source adapter: " + path)
    text = source.decode("utf-8-sig")
    if b"\0" in source or len(source) > 1_000_000:
        raise GraphContractError("resource must be bounded UTF-8 source")
    revision = {"digest": hashlib.sha256(source).hexdigest(), "algorithm": "sha256"}
    facts = {"extractor": RESOURCE_EXTRACTOR, "imports": [], "raw_calls": [],
             "parse_ok": None, "format": PurePosixPath(path).suffix.lower(),
             "classification": "path-based", "semantic_status": "not_assessed"}
    if path.endswith(".json"):
        try:
            json.loads(text)
            facts["parse_ok"] = True
        except ValueError:
            facts["parse_ok"] = False
    owner, identifier = file_id(path), symbol_id(path, kind)
    file_node = {"id": owner, "kind": "file", "path": path, "source": revision,
                 "facts": facts, "fact_status": {k: "fresh" for k in facts}, "facts_class": "extracted"}
    resource = {"id": identifier, "kind": kind, "symbol": kind, "path": path, "source": revision,
                "start_line": 1, "end_line": max(1, len(text.splitlines())),
                "facts": {"extractor": RESOURCE_EXTRACTOR, "semantic_status": "not_assessed"},
                "fact_status": {"extractor": "fresh", "semantic_status": "fresh"}, "facts_class": "extracted"}
    edge = {"id": edge_id(owner, "contains", identifier), "kind": "contains", "src": owner, "dst": identifier,
            "status": "active", "facts": {"extractor": RESOURCE_EXTRACTOR},
            "fact_status": {"extractor": "fresh"}, "facts_class": "extracted"}
    return {"nodes": [file_node, resource], "edges": [edge]}
