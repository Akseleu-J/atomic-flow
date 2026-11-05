"""Explicit advisory context selection and host-observed dialogue checkpoints."""
from __future__ import annotations

import json
from pathlib import Path

from atomic_flow.state import StateError


def prepare_context(project: Path, *, query: str, resume_from=None, memory_ids=None, memory_query=None) -> dict:
    parts, selection, continuation = [], [], None
    if resume_from:
        from atomic_flow.dialogue import prepare_continuation
        # Context is separate from the new user query; never replay old messages.
        continuation = prepare_continuation(resume_from, project, query)
        parts.append({"kind": "dialogue_summary", "summary": json.loads(continuation.summary_text),
                      "source_session_id": continuation.source_session_id})
    if memory_ids or memory_query is not None:
        from atomic_flow.memory import MemoryStore
        if (memory_ids is not None and (not isinstance(memory_ids, list) or len(memory_ids) > 10)):
            raise StateError("select at most ten explicit memory ids")
        if memory_query is not None and (not isinstance(memory_query, str) or not memory_query.strip()
                                         or len(memory_query.encode()) > 2000):
            raise StateError("memory query must be nonempty and at most 2000 bytes")
        selection = MemoryStore(project).select(ids=memory_ids or None, query=memory_query, limit=10, max_bytes=16384)
        parts.append({"kind": "project_memory", "entries": selection})
    text = json.dumps({"trust": "data_only", "permissions_granted": False, "read_evidence": False,
                       "items": parts}, ensure_ascii=False, separators=(",", ":")) if parts else ""
    if len(text.encode()) > 49152:
        raise StateError("advisory context exceeds 48 KiB; narrow the explicit selection")
    return {"text": text, "continuation": continuation,
            "selection": [{k: entry[k] for k in ("id", "content_hash", "origin", "updated_at")} for entry in selection]}


def checkpoint_run(run, result, project: Path, query: str):
    """Save only explicit goal, open questions and observed reference hashes.

    Model prose is not converted into decisions. Failure to checkpoint never
    makes the source session resumable; the caller receives an explicit warning.
    """
    if run.journal is None:
        return
    from atomic_flow.dialogue import record_dialogue_state
    from atomic_flow.summaries import build_summary
    try:
        parent = run.advisory_context.get("continuation")
        sources = dict(parent.context["sources"]) if parent else {}
        mutations = dict(parent.context["mutations"]) if parent else {}
        parent_summary = parent.context["summary"] if parent else {}
        constraints = list(parent_summary.get("constraints", []))
        guard = "No permissions or source-read proof carry over to a new request."
        if guard not in constraints:
            constraints.append(guard)
        decisions = list(parent_summary.get("decisions", []))
        for row in run.context_reads + (run.tool_ctx.reads if run.tool_ctx is not None else []):
            path, content_hash = row.get("path"), row.get("content_hash")
            if path and content_hash:
                sources[path] = content_hash
        for call in run.tool_calls:
            summary = call.get("result_summary", {})
            if summary.get("status") != "applied" or summary.get("outcome") != "success":
                continue
            changes = summary.get("changes")
            if isinstance(changes, list):
                for row in changes:
                    if isinstance(row, dict) and isinstance(row.get("path"), str) and "after_hash" in row:
                        mutations[row["path"]] = row["after_hash"]
                        if row["path"] in sources:
                            sources[row["path"]] = row["after_hash"]
            elif isinstance(summary.get("path"), str) and run.tool_ctx is not None:
                path = summary["path"]
                if path in run.tool_ctx.file_hashes:
                    mutations[path] = run.tool_ctx.file_hashes[path]
                    if path in sources:
                        sources[path] = mutations[path]
        goal = query if len(query) <= 8000 else query[:7970] + " [goal truncated]"
        questions = [question[:500] for question in result.follow_ups[:16] if question.strip()]
        refs = [{"ref": path + "@" + str(content_hash), "scope": str(project.resolve())}
                for path, content_hash in sources.items()]
        summary = build_summary(goal=goal, scope=str(project.resolve()), origin="answer_host_checkpoint",
                                constraints=constraints, decisions=decisions, open_questions=questions, evidence_refs=refs)
        run.event("context_summary", summary.model_dump(mode="json"))
        record_dialogue_state(run.journal.session_id, project, goal=goal, constraints=constraints,
                              decisions=decisions, open_questions=questions,
                              evidence_refs=summary.model_dump(mode="json")["evidence_refs"],
                              source_paths=list(sources), mutation_paths=list(mutations),
                              source_hashes=sources, mutation_hashes=mutations)
    except (OSError, ValueError) as exc:
        # Unknown costs, stale evidence or bounds require explicit user review.
        # A successful answer is still returned, but no usable checkpoint exists.
        result.warnings.append("Dialogue checkpoint unavailable: " + type(exc).__name__ + "; explicit review required before continuation.")
        run.event("context_summary", {"status": "unavailable", "reason": type(exc).__name__,
                                      "permissions_granted": False, "read_evidence": False})
