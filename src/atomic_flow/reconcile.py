"""Host-only, evidence-backed reconciliation of unknown charges and effects.

Closes exactly what the owner confirms — never replays a provider or tool call.
A reconciliation touches up to three durable artifacts (task event, spend ledger
row, session observation). Each write is individually durable and idempotent:
re-running the same operation with the same evidence repairs artifacts a crash
left unwritten instead of duplicating them. Model text, plan approval or a new
task never constitute evidence; the host supplies an evidence reference (file
bytes are hashed, never stored) plus actor and reason.
"""
from __future__ import annotations

import hashlib
import math

from atomic_flow.agents.control import task_lease
from atomic_flow.agents.task import load as load_task
from atomic_flow.agents.task import save as save_task
from atomic_flow.budget import SpendLedger
from atomic_flow.sessions import SessionJournal
from atomic_flow.state import StateError

SCOPES = ("charge", "effect")


def evidence_digest(evidence: str | bytes) -> str:
    if isinstance(evidence, str):
        evidence = evidence.encode("utf-8")
    return hashlib.sha256(evidence).hexdigest()


def _session_event(journal: SessionJournal, call_id: str, payload: dict):
    for event in journal.read().events:
        if (event["event_type"] == "host_reconciliation" and event.get("call_id") == call_id
                and event["payload"].get("reconciliation_id") == payload["reconciliation_id"]):
            return event
    return journal.append("host_reconciliation", payload, call_id=call_id)


def reconcile_call(task_id: str, call_id: str, scope: str, *, evidence: str | bytes,
                   actor: str, reason: str, cost_usd: float | None = None,
                   daily_limit_usd: float = 10.0) -> dict:
    """Settle one unknown charge or effect hold with durable, idempotent evidence.

    ``scope="charge"`` requires the task budget event for ``call_id`` to be in
    status ``unknown`` and an explicit settled ``cost_usd`` (0.0 means confirmed
    free, not forgotten). ``scope="effect"`` requires session observations for
    ``call_id``; it clears the effect hold without touching money accounting.
    """
    if scope not in SCOPES:
        raise StateError(f"reconciliation scope must be one of {SCOPES}")
    if not actor or not reason:
        raise StateError("reconciliation requires an explicit actor and reason")
    if scope == "charge":
        if type(cost_usd) not in (int, float) or not math.isfinite(cost_usd) or cost_usd < 0:
            raise StateError("charge reconciliation requires an explicit settled cost_usd >= 0")
    evidence_sha = evidence_digest(evidence)
    reconciliation_id = "reconcile_" + hashlib.sha256(
        f"{task_id}|{call_id}|{scope}|{evidence_sha}".encode()).hexdigest()
    payload = {"scope": scope, "reconciliation_id": reconciliation_id,
               "evidence_sha256": evidence_sha, "actor": actor, "reason": reason}

    with task_lease(task_id):
        task = load_task(task_id)
        updated = {"task": False, "ledger": False, "session": False}
        try:
            journal = SessionJournal.open(task_id)
        except FileNotFoundError:
            journal = None
        if journal is not None:
            from pathlib import Path
            snapshot = journal.read()
            if Path(snapshot.events[0]["payload"]["project"]).resolve() != Path(task.project).resolve():
                raise StateError("session project does not match the task")

        if scope == "charge":
            events = [event for event in task.budget_events if event.get("id") == call_id]
            if not events:
                raise StateError(f"task {task_id} has no budget event {call_id}")
            if len(events) != 1:
                raise StateError("task has duplicate call identities; inspect accounting")
            event = events[0]
            existing = event.get("reconciliation")
            if existing and existing.get("reconciliation_id") != reconciliation_id:
                raise StateError("call already reconciled under different evidence; inspect the ledger")
            settled = float(cost_usd)
            if existing and existing.get("settled_cost_usd") != settled:
                raise StateError("call already reconciled with a different settled cost")
            if not existing:
                if event.get("status") != "unknown":
                    raise StateError("only unknown charges can be reconciled")
                # Persist intent while keeping accounting blocked. Only the final
                # task write, after ledger and session, clears this unknown hold.
                event["reconciliation"] = {**payload, "settled_cost_usd": settled, "pending": True}
                save_task(task)
                updated["task"] = True
            SpendLedger(daily_limit_usd).settle(
                task_id=task_id, call_id=call_id, cost_usd=settled,
                status="reconciled", reconciliation_id=reconciliation_id)
            updated["ledger"] = True

        if scope == "effect":
            if journal is None:
                raise StateError("effect reconciliation requires the session observations") from None
            observations = [item for item in snapshot.events if item.get("call_id") == call_id]
            previous = [item for item in observations if item["event_type"] == "host_reconciliation"
                        and item["payload"]["scope"] == "effect"]
            if previous:
                if any(item["payload"]["reconciliation_id"] != reconciliation_id for item in previous):
                    raise StateError("effect already reconciled under different evidence")
            else:
                from atomic_flow.sessions import SessionSnapshot
                effects = [item for item in observations if item["event_type"] in
                           {"tool_request", "tool_result", "tool_effect_unknown"}]
                if call_id not in SessionSnapshot(task_id, effects, False, 0).unresolved_call_ids:
                    raise StateError("effect reconciliation requires an actual unresolved effect call")
        if journal is not None:
            _session_event(journal, call_id, payload)
            updated["session"] = True
        if scope == "charge" and event["reconciliation"].get("pending"):
            previous_cost = event.get("cost_usd") or 0.0
            task.budget_spent += settled - previous_cost
            event.update(status="completed", cost_usd=settled)
            event["reconciliation"]["pending"] = False
            save_task(task)
            updated["task"] = True

    return {"task_id": task_id, "call_id": call_id, "scope": scope,
            "reconciliation_id": reconciliation_id, "evidence_sha256": evidence_sha,
            "settled_cost_usd": float(cost_usd) if scope == "charge" else None,
            "artifacts_updated": updated}
