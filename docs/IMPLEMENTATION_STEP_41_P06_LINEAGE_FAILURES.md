# Package 40 (P06 slices 2–4): lineage predicate, failure table, publication graph sync

Date: 25 October 2026. Source: clean on top of `35573f6` (branch
`experimental`). Model calls = 0. Follows [ADR-0001](ADR-0001-authorization-envelope.md)
and the P06 assignment in [ASSISTANT_HANDOFF.md](ASSISTANT_HANDOFF.md) §7.

## Implemented

1. **`agents/replan_lineage.py::lineage_preserved(obligations, new_steps)`**
   — pure predicate that a replacement plan keeps every owner obligation:
   criteria coverage (a lost criterion blocks), scope containment (widening
   refused), ancestor budget caps (a step cap above the ancestor cap
   refused), required final checks present. Malformed input fails closed.
   Grounded in the real Goal contract (`acceptance_criteria` ≤8,
   `scope_paths`, `max_budget_usd`); model-text similarity is never
   consulted.
2. **`agents/failure_policy.py::classify(failure)`** — explicit failure-kind
   → mandated decision table: deterministic test failure → bounded repair
   (≤25 % of remaining budget, and only with envelope+lineage+fresh failing
   evidence); source conflict / stale approval → reread + fresh review;
   unknown submission/charge/effect → durable hold; missing dependency /
   authority expansion → user decision; scope violation / budget / deadline /
   no-progress / cancel → stop. Unknown kinds stop — no default continue.
3. **`graph/sync.py::sync_project_graph` + TaskRunner wiring** — the
   publication hot path now delta-syncs the structural graph store
   (`.atomic_flow/graph.json`): first publication creates it from current
   sources; later publications apply one file delta per changed path
   (content-CAS, closure relink via the P02 resolver) instead of a full
   rebuild. Delta failure degrades to an explicit reasoned fallback rebuild;
   a failed sync never flips a completed publication into a failure — it is
   recorded on the task (`budget_events`) and in a sidecar
   `graph-sync.jsonl`.

## Evidence

- `tests/test_replan_lineage.py` — 6 passed (inherit-keep passes; lost
  criterion / scope widening / ancestor-cap breach / missing required check /
  malformed input refused with reasons).
- `tests/test_failure_policy.py` — 15 passed matrix incl. unknown-kind stop
  and no-budget repair stop.
- `tests/test_graph_sync.py` — 3 passed: create→delta parity with importer
  relink and no reparse (`parsed_files == 1` on a two-file project), broken
  delta path degrades to reasoned fallback restoring parity with disk,
  delete removes the file and its edges.
- **§7.7 scripted journey** `tests/test_p06_journey.py` — 1 passed: real
  TaskRunner run (scripted backend) edits `sample.py`, host checks pass,
  changeset publishes, the hot path syncs the graph by delta and the store
  equals a clean build of the published bytes; the sync is recorded on the
  task without fallback.
- Neighbours (task/task_execution/internal_replan/task_changeset) — together
  56 passed; ruff/compile clean.

## Not claimed

The two predicates are not yet consulted by the live replan path (the
existing schema-2 internal-replan checks remain authoritative; wiring these
in with `replan_failures` recording is the next slice). The failure table is
a contract, not yet the sole decision source in `task_loop`. Adversarial
matrix rows beyond existing suites (crash-resume-during-sync, malicious
instruction during repair) are enumerated for the chapter's acceptance pass.
Graph sync does not yet feed retrieval (legacy tree remains the read path).
