"""Read-only Understand operations over an admitted current source snapshot."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from atomic_flow.activation.redact import redact
from atomic_flow.graph.adapter_resource import resource_kind
from atomic_flow.graph.impact import context_pack, verification_plan
from atomic_flow.graph.project import clean_build, project_files
from atomic_flow.graph.schema import GraphContractError
from atomic_flow.graph.semantic import entry_points, file_dependencies, layers, tour

OPERATIONS = ("inspect", "search", "explain", "chat", "impact", "tour")


def _scope_inspection(result, node, build, files):
    """Scope the projection, retaining the revision of the full admitted graph.

    Global diagnostics are counted rather than attached to a one-node request.
    Entry-point membership still comes from the complete graph, never a rebuild
    of the selected file that would change the meaning of the heuristic.
    """
    path = node["path"]
    unresolved = [item for item in build["unresolved"] if item.startswith(path + ": ")]
    omissions = [item for item in result["omissions"] if item.get("path") == path]
    excluded = {"nodes": len(build["nodes"]) - 1,
                "edges": len(build["edges"]) - len(result["edges"]),
                "unresolved": len(build["unresolved"]) - len(unresolved),
                "unsupported_files": len(result["omissions"]) - len(omissions)}
    result["projection_scope"] = {"kind": "node", "node_id": node["id"], "paths": [path],
        "source_revision_scope": "all_admitted_files", "global_counts": {
            "files": len(files), "nodes": len(build["nodes"]), "edges": len(build["edges"]),
            "unresolved": len(build["unresolved"]), "unsupported_files": len(result["omissions"])}}
    result["unresolved"] = unresolved
    result["omissions"] = omissions
    if any(excluded.values()):
        result["omissions"].append({"reason": "outside_requested_scope", "counts": excluded})
    for metadata in (result["layers"], result["entry_points"]):
        metadata["source_digests"] = {key: value for key, value in metadata["source_digests"].items() if key == path}
        metadata["projection_scope"] = "requested_node_file; classification computed over full graph"
    result["layers"]["groups"] = {key: [path] for key, paths in result["layers"]["groups"].items() if path in paths}
    result["entry_points"]["entry_files"] = [item for item in result["entry_points"]["entry_files"] if item == path]


def _bound_scoped_inspection(result, byte_cap):
    """Drop whole projection records only, with explicit per-field accounting."""
    result["output_complete"] = True
    for field in ("edges", "unresolved", "nodes"):
        if len(json.dumps(result, ensure_ascii=False).encode()) <= byte_cap:
            return
        original = result[field]
        omission = {"reason": "output_byte_cap", "field": field, "items": 0}
        result["omissions"].append(omission)
        result["output_complete"] = False
        low, high = 0, len(original)
        while low < high:
            count = (low + high + 1) // 2
            result[field] = original[:count]
            omission["items"] = len(original) - count
            if len(json.dumps(result, ensure_ascii=False).encode()) <= byte_cap:
                low = count
            else:
                high = count - 1
        result[field] = original[:low]
        omission["items"] = len(original) - low
        if omission["items"] == 0:
            result["omissions"].remove(omission)
    if len(json.dumps(result, ensure_ascii=False).encode()) > byte_cap:
        raise GraphContractError("graph metadata exceeds output byte cap; narrow request")


def graph_operation(project: Path, operation: str, *, query="", node_id="", paths=None,
                    byte_cap=65536, semantic=None, cancel_check=lambda: None) -> dict:
    """No implicit model calls, executables, read-proof grants or mutations.

    Chat is extractive source-backed Q&A. A caller may supply an existing
    semantic result; its scope/digests are checked before inferred text is used.
    Missing semantic material is explicit rather than synthesized explanation.
    """
    if operation not in OPERATIONS or not isinstance(query, str) or len(query) > 4096:
        raise GraphContractError("unsupported graph operation or unbounded query")
    if type(byte_cap) is not int or not 0 < byte_cap <= 1_000_000:
        raise GraphContractError("graph output byte cap invalid")
    if (not isinstance(node_id, str) or len(node_id) > 1024 or paths is not None and
            (not isinstance(paths, list) or len(paths) > 64 or any(not isinstance(p, str) for p in paths))):
        raise GraphContractError("graph node/path selection invalid")
    snapshot = project_files(Path(project), suffix=None, cancel_check=cancel_check)
    files = {p: raw for p, raw in snapshot.items() if p.endswith((".py", ".pyi")) or resource_kind(p)}
    omitted = [{"path": p, "reason": "unsupported_adapter"} for p in sorted(set(snapshot) - files.keys())]
    build = clean_build(files)
    nodes = {n["id"]: n for n in build["nodes"]}
    revision = hashlib.sha256(json.dumps({p: hashlib.sha256(raw).hexdigest() for p, raw in sorted(files.items())},
                                       sort_keys=True).encode()).hexdigest()
    result = {"schema_version": 1, "operation": operation, "source_revision": revision,
              "data_only": True, "read_proof": False, "semantic_correctness": "not_assessed",
              "omissions": omitted, "unresolved": build["unresolved"]}
    valid_semantic = {}
    if semantic is not None:
        from atomic_flow.graph.enrichment import _validate
        expected_prefix = str(Path(project).resolve()) + "::"
        if (not isinstance(semantic, dict) or not isinstance(semantic.get("scope"), str)
                or not semantic["scope"].startswith(expected_prefix)):
            raise GraphContractError("semantic project scope mismatch")
        artifacts = semantic.get("artifacts")
        if not isinstance(artifacts, dict):
            raise GraphContractError("semantic artifacts missing")
        current_dependencies = file_dependencies(build)
        for path, artifact in artifacts.items():
            if (not isinstance(artifact, dict) or artifact.get("status") != "fresh"
                    or artifact.get("verification") != "evidence_only"
                    or not isinstance(artifact.get("cache_key"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", artifact["cache_key"])):
                raise GraphContractError("semantic artifact provenance invalid")
            if path not in files or artifact.get("source_digest") != hashlib.sha256(files[path]).hexdigest():
                raise GraphContractError("semantic source is stale; refresh before use")
            _validate(artifact.get("result"), files[path], {n["id"]: n for n in build["nodes"] if n["path"] == path})
            if not isinstance(artifact.get("dependency_digests"), dict) or path not in artifact["dependency_digests"]:
                raise GraphContractError("semantic dependency provenance missing")
            required_dependencies = (set(files) if build["unresolved"] else
                                     {path, *current_dependencies.get(path, [])})
            if not required_dependencies <= artifact["dependency_digests"].keys():
                raise GraphContractError("semantic dependency topology is stale; refresh before use")
            for dependency, expected in artifact.get("dependency_digests", {}).items():
                if dependency not in files or hashlib.sha256(files[dependency]).hexdigest() != expected:
                    raise GraphContractError("semantic dependency is stale; refresh before use")
            valid_semantic[path] = artifact
    if operation == "inspect":
        if node_id:
            if node_id not in nodes:
                raise GraphContractError("graph node unavailable")
            result["nodes"] = [nodes[node_id]]
        else:
            result["nodes"] = build["nodes"]
        result["edges"] = [e for e in build["edges"] if not node_id or node_id in {e["src"], e["dst"]}]
        result["layers"] = layers(files)
        result["entry_points"] = entry_points(files)
        if node_id:
            _scope_inspection(result, nodes[node_id], build, files)
    elif operation == "impact":
        selected = list(paths or [])
        if not selected or any(path not in files for path in selected):
            raise GraphContractError("impact requires admitted current file paths")
        python = {p: raw for p, raw in files.items() if p.endswith((".py", ".pyi"))}
        result["context_pack"] = context_pack(python, [p for p in selected if p in python], byte_cap=byte_cap) if any(p in python for p in selected) else None
        result["verification_plan"] = verification_plan(files, selected)
    elif operation == "tour":
        structural = tour(files)
        result["tour"] = {**structural, "steps": [{**step, "source_digest": hashlib.sha256(files[step["file"]]).hexdigest(),
            "node_id": "file:" + step["file"], "summary": valid_semantic.get(step["file"], {}).get("result", {}).get("summary"),
            "narrative_status": "fresh" if step["file"] in valid_semantic else "not_assessed"} for step in structural["steps"]]}
    else:
        terms = set(re.findall(r"\w+", query.lower()))
        candidates = [nodes[node_id]] if node_id in nodes else list(nodes.values())
        if operation == "explain" and node_id and node_id not in nodes:
            raise GraphContractError("graph node unavailable")
        ranked = sorted(candidates, key=lambda n: (-sum(t in (n["path"] + " " + n.get("symbol", "")).lower() for t in terms), n["id"]))
        evidence = []
        for node in ranked[:30]:
            cancel_check()
            path = node["path"]
            lines = files[path].decode("utf-8-sig").splitlines(keepends=True)
            start, end = node.get("start_line", 1), node.get("end_line", len(lines))
            if not lines:
                continue
            quote = redact("".join(lines[start - 1:end]))
            evidence.append({"node_id": node["id"], "path": path, "start_line": start, "end_line": end,
                             "source_digest": node["source"]["digest"], "quote": quote,
                             "summary": valid_semantic.get(path, {}).get("result", {}).get("summary"),
                             "summary_status": "fresh" if path in valid_semantic else "not_assessed"})
        result["evidence"] = evidence
        result["answer_method"] = "extractive current-source evidence; no inferred semantic verdict"
    if project_files(Path(project), suffix=None, cancel_check=cancel_check) != snapshot:
        raise GraphContractError("source changed during graph operation")
    if operation == "inspect" and node_id:
        _bound_scoped_inspection(result, byte_cap)
        return result
    encoded = json.dumps(result, ensure_ascii=False).encode()
    if len(encoded) > byte_cap:
        # Never trim inside an evidence span and leave a misleading complete flag.
        key = next((key for key in ("evidence", "nodes") if key in result), None)
        if key:
            removed = 0
            while result[key] and len(json.dumps(result, ensure_ascii=False).encode()) > byte_cap:
                result[key].pop()
                removed += 1
            result["omissions"].append({"reason": "output_byte_cap", "items": removed})
        else:
            raise GraphContractError("graph output exceeds byte cap; narrow requested paths")
    result["output_complete"] = not any(item.get("reason") == "output_byte_cap" for item in result["omissions"])
    if len(json.dumps(result, ensure_ascii=False).encode()) > byte_cap:
        raise GraphContractError("graph metadata exceeds output byte cap; narrow request")
    return result
