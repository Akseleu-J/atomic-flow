"""Budgeted semantic enrichment with revision-bound evidence and transactional cache.

The analyzer is a trusted host callback. Its text is advisory untrusted data; no
provider, executable, credentials or authorization are selected by this module.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

from atomic_flow.activation.redact import redact
from atomic_flow.graph.project import clean_build
from atomic_flow.graph.schema import GraphContractError
from atomic_flow.graph.semantic import assemble, file_dependencies, semantic_hashes

PIPELINE_VERSION = "semantic-pipeline/2"


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass
class SemanticBudget:
    max_calls: int
    calls: int = 0

    def reserve(self):
        if type(self.max_calls) is not int or self.max_calls < 0 or self.calls >= self.max_calls:
            raise GraphContractError("semantic call budget exhausted")
        self.calls += 1  # Never refund a failed/unknown callback outcome.


def _validate(result, source, nodes):
    if not isinstance(result, dict) or set(result) != {"summary", "tags", "evidence"}:
        raise GraphContractError("semantic result needs summary/tags/evidence only")
    if len(json.dumps(result, ensure_ascii=False).encode()) > 16_384:
        raise GraphContractError("semantic result exceeds byte cap")
    if not isinstance(result["summary"], str) or not result["summary"].strip():
        raise GraphContractError("semantic summary missing")
    if (not isinstance(result["tags"], list) or len(result["tags"]) > 32
            or any(not isinstance(tag, str) or len(tag) > 80 for tag in result["tags"])):
        raise GraphContractError("invalid semantic tags")
    if not isinstance(result["evidence"], list) or not 1 <= len(result["evidence"]) <= 32:
        raise GraphContractError("semantic evidence missing")
    lines = source.decode("utf-8-sig").splitlines(keepends=True)
    for span in result["evidence"]:
        if not isinstance(span, dict) or set(span) != {"node_id", "start_line", "end_line", "quote"}:
            raise GraphContractError("invalid semantic evidence shape")
        start, end = span["start_line"], span["end_line"]
        node = nodes.get(span["node_id"])
        if (node is None or type(start) is not int or type(end) is not int
                or not 1 <= start <= end <= len(lines)
                or not isinstance(span["quote"], str) or not span["quote"].strip()
                or span["quote"] != "".join(lines[start - 1:end])):
            raise GraphContractError("semantic evidence is not exact current source")
        if node["kind"] != "file" and not node["start_line"] <= start <= end <= node["end_line"]:
            raise GraphContractError("semantic evidence lies outside cited node")


def enrich(files: dict[str, bytes], *, project_scope: str, identity: dict,
           analyzer, budget: SemanticBudget, cache: dict, dependencies=None,
           cancel_check=lambda: None) -> dict:
    """Analyze invalid files only; publish cache after every stage succeeds.

    identity freezes extractor/prompt/model/config/language. Dependency lists
    are explicit host input; omitted relations are not claimed to be resolved.
    No cache hit grants read-before-edit proof or semantic correctness.
    """
    required = {"extractor", "prompt", "model", "config", "language"}
    if (not isinstance(project_scope, str) or not project_scope.strip()
            or set(identity) != required or any(not isinstance(v, str) or not v for v in identity.values())):
        raise GraphContractError("semantic cache identity incomplete")
    if len(files) > 4096 or sum(map(len, files.values())) > 8_000_000 or any(len(v) > 1_000_000 for v in files.values()):
        raise GraphContractError("semantic admitted-source byte/file cap exceeded")
    cancel_check()
    build = clean_build(files)
    inferred_dependencies = file_dependencies(build)
    hashes = {path: semantic_hashes(raw) for path, raw in files.items()}
    digests = {n["path"]: n["source"]["digest"] for n in build["nodes"] if n["kind"] == "file"}
    staged, artifacts = {}, {}
    before_calls = budget.calls
    for path in sorted(files):
        cancel_check()
        deps = sorted({path, *inferred_dependencies.get(path, []), *(dependencies or {}).get(path, [])})
        # Unknown/dynamic relations cannot justify a narrow invalidation claim.
        # Conservatively bind every file until the resolver can prove a closure.
        if build["unresolved"]:
            deps = sorted(files)
        if any(dep not in files for dep in deps):
            raise GraphContractError("semantic dependency missing from admitted snapshot")
        nodes = [n for n in build["nodes"] if n["path"] == path]
        binding = {"pipeline": PIPELINE_VERSION, "scope": project_scope, "path": path,
                   "identity": identity, "dependencies": {p: digests[p] for p in deps},
                   "semantic_hashes": hashes[path], "resolver": "resolved-file-closure/1",
                   "structure": nodes}
        key = _hash(binding)
        if key in cache:
            result = copy.deepcopy(cache[key])
        else:
            budget.reserve()
            result = analyzer(copy.deepcopy({**binding, "source": redact(files[path].decode("utf-8-sig"))}))
        cancel_check()
        _validate(result, files[path], {n["id"]: n for n in nodes})
        staged[key] = copy.deepcopy(result)
        artifacts[path] = {"cache_key": key, "source_digest": digests[path], "status": "fresh",
                           "semantic_hashes": hashes[path], "dependencies": deps,
                           "dependency_digests": {p: digests[p] for p in deps},
                           "facts_class": "inferred", "verification": "evidence_only",
                           "result": copy.deepcopy(result)}
    # Assembly/architecture/tour/review remain deterministic and digest-bound.
    structural = assemble(files, build, artifacts)
    cancel_check()
    cache.update(staged)
    return {"pipeline": PIPELINE_VERSION, "scope": project_scope, "artifacts": artifacts,
            "structural": structural, "calls": budget.calls - before_calls,
            "review": {"evidence": "passed", "semantic_correctness": "not_assessed",
                       "coverage": sorted(files), "omissions": [],
                       "surviving_symbols": sorted(n["id"] for n in build["nodes"] if n["kind"] != "file"),
                       "dependency_scope": "all admitted files when unresolved; otherwise resolved closure plus explicit host dependencies",
                       "unresolved": build["unresolved"]}}
