"""Explicit advisory dialogue state; continuation never replays stored actions."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from atomic_flow.agents.source import digest
from atomic_flow.agents.tools.file_paths import admitted_path
from atomic_flow.sessions import SessionJournal
from atomic_flow.state import StateError

MAX_REFERENCES = 64
MAX_CONTEXT_BYTES = 24 * 1024


def _path(project: Path, rel: str):
    return admitted_path(SimpleNamespace(project_root=project, private_workspace=False,
                                        tree_ignore=[], respect_gitignore=True), rel)


def _hashes(project: Path, paths) -> dict:
    if isinstance(paths, (str, bytes)):
        raise StateError("source paths must be a list")
    paths = list(paths)
    if len(paths) > MAX_REFERENCES or any(not isinstance(rel, str) for rel in paths):
        raise StateError("too many or invalid source references")
    result = {}
    for rel in paths:
        path = _path(project, rel)
        result[rel] = digest(path.read_bytes()) if path.exists() else None
    return result


def _safe_snapshot(journal: SessionJournal, project: Path):
    snapshot = journal.read()
    if snapshot.events[0]["payload"].get("project") != str(project.resolve()):
        raise StateError("session belongs to a different project")
    if snapshot.incomplete_tail or snapshot.unresolved_call_ids:
        raise StateError("session has interrupted or unresolved effects/charges; inspect before continuation; never replay")
    if not any(event["event_type"] in {"completed", "fork_created"} for event in snapshot.events):
        raise StateError("session has no completed turn; continuation requires a reviewed host checkpoint")
    return snapshot


def record_dialogue_state(session_id: str, project: Path, *, goal: str, constraints=(), decisions=(), open_questions=(), evidence_refs=(),
                          source_paths=(), mutation_paths=(), source_hashes: dict | None = None,
                          mutation_hashes: dict | None = None) -> dict:
    """Host API: persist explicit facts; model transcripts are never inferred as decisions."""
    from atomic_flow.summaries import build_summary

    project = Path(project).resolve()
    journal = SessionJournal.open(session_id)
    with journal._lease():
        snapshot = _safe_snapshot(journal, project)
        summary = build_summary(goal=goal, constraints=list(constraints), decisions=list(decisions), open_questions=list(open_questions),
                                evidence_refs=list(evidence_refs), scope=str(project), origin="dialogue_checkpoint")
        summary_data = summary.model_dump(mode="json")
        sources, mutations = _hashes(project, source_paths), _hashes(project, mutation_paths)
        for observed, supplied in ((sources, source_hashes), (mutations, mutation_hashes)):
            if supplied is not None and (not isinstance(supplied, dict) or observed != supplied):
                raise StateError("observed source/mutation hashes changed before checkpoint")
        state = {"schema_version": 1, "project": str(project), "summary": summary_data, "kind": "completed_turn",
                 "sources": sources, "mutations": mutations,
                 "trust": "data_only", "permissions_granted": False, "read_evidence": False,
                 "observed_sequence": len(snapshot.events)}
        if len(json.dumps(state, ensure_ascii=False).encode()) > MAX_CONTEXT_BYTES:
            raise StateError("dialogue state exceeds bounded context")
        # append() takes its own OS lease, so release before that operation. The
        # checkpoint binds observed_sequence; later observations invalidate it.
    journal.append("dialogue_state", state, expected_sequence=state["observed_sequence"])
    return state


@dataclass(frozen=True)
class ContinuationContext:
    source_session_id: str
    query: str
    context: dict
    summary_text: str


def merge_continuation_evidence(context: ContinuationContext | None, source_hashes: dict, mutation_hashes: dict) -> tuple[dict, dict]:
    """Keep parent references; only new host observations may supersede their hashes."""
    sources = dict(context.context["sources"]) if context else {}
    mutations = dict(context.context["mutations"]) if context else {}
    sources.update(source_hashes)
    mutations.update(mutation_hashes)
    # A host-observed mutation changes the revision of an earlier source reference.
    for rel in sources.keys() & mutation_hashes.keys():
        sources[rel] = mutation_hashes[rel]
    if len(sources) > MAX_REFERENCES or len(mutations) > MAX_REFERENCES:
        raise StateError("cumulative dialogue evidence exceeds reference limits")
    return sources, mutations


def prepare_continuation(session_id: str, project: Path, new_query: str) -> ContinuationContext:
    from atomic_flow.summaries import StructuredSummary, render_summary

    if not isinstance(new_query, str) or not new_query.strip() or len(new_query.encode()) > MAX_CONTEXT_BYTES:
        raise StateError("continuation requires a bounded new query")
    project = Path(project).resolve()
    journal = SessionJournal.open(session_id)
    with journal._lease():
        snapshot = _safe_snapshot(journal, project)
        state = snapshot.events[-1]["payload"]
        if snapshot.events[-1]["event_type"] != "dialogue_state":
            raise StateError("session requires a current explicit dialogue checkpoint")
        expected_keys = {"schema_version", "project", "summary", "kind", "sources", "mutations", "trust",
                         "permissions_granted", "read_evidence", "observed_sequence"}
        if (set(state) != expected_keys or type(state["schema_version"]) is not int or state["schema_version"] != 1
                or state["project"] != str(project) or state["observed_sequence"] != len(snapshot.events) - 1
                or state["kind"] not in {"completed_turn", "fork"}
                or state["trust"] != "data_only" or state["permissions_granted"] is not False
                or state["read_evidence"] is not False):
            raise StateError("invalid or unsupported dialogue checkpoint")
        for key in ("sources", "mutations"):
            hashes = state[key]
            if not isinstance(hashes, dict) or _hashes(project, hashes) != hashes:
                raise StateError("dialogue source/mutation evidence changed; refresh checkpoint after review")
        summary = StructuredSummary.model_validate(state["summary"])
        context = {**state, "source_session_id": session_id,
                   "source_digest": hashlib.sha256(journal.path.read_bytes()).hexdigest(), "replay_supported": False}
        return ContinuationContext(session_id, new_query, context, render_summary(summary))


def fork_session(source_session_id: str, project: Path, new_query: str, *, session_id: str | None = None) -> SessionJournal:
    prepared = prepare_continuation(source_session_id, project, new_query)
    journal = SessionJournal.create(Path(project), session_id=session_id,
                                    metadata={"forked_from": source_session_id, "replay_supported": False})
    journal.append("continuation", prepared.context)
    journal.append("user_input", {"text": new_query})
    journal.append("fork_created", {"parent": source_session_id, "source_digest": prepared.context["source_digest"],
                                    "execution_performed": False})
    state = {key: value for key, value in prepared.context.items()
             if key not in {"source_session_id", "source_digest", "replay_supported"}}
    state.update(kind="fork", observed_sequence=len(journal.read().events))
    journal.append("dialogue_state", state)
    return journal
