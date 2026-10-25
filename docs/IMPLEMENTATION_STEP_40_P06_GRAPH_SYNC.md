# Package 40 (P06 §7.5 leg): publication hot path delta-syncs the structural graph

Date: 25 October 2026. Source: clean on top of `35573f6` (branch
`experimental`). Model calls = 0. Follows ADR-0001 and handoff §7 item 5
("observe → … → graph/source update → decision").

## Implemented

- **`src/atomic_flow/graph/sync.py::sync_project_graph(project, changed)`** —
  the hot-path integration of the P02 delta engine: first use builds the
  project graph store (`.atomic_flow/graph.json`) from current sources;
  afterwards each publication applies **one file delta per changed path**
  (content CAS against the stored digest, closure relink of inferred edges —
  no re-parsing of untouched files). Failures never roll back or fail the
  already-published source: a broken delta degrades to an explicit
  reason-coded `fallback_rebuild`, and even that failure is only recorded in
  the `.atomic_flow/graph-sync.jsonl` sidecar.
- **`TaskRunner` publication wiring** — after `changeset.publish(...)`
  succeeds, `_sync_graph` syncs the store with the published bytes and
  appends a `graph_sync` event (deltas/created/fallback/failed) to the task
  record. Graph problems can never flip a completed publication into a
  failure.
- **Journey test (`tests/test_p06_journey.py`)** — handoff §7.7 first leg:
  scripted multi-turn run (parser/answerer/critic) edits a file through the
  real tool loop, host checks pass, the changeset publishes, and the store
  **equals a clean build of the published sources** (nodes-with-facts and
  edges), with the sync event recorded on the task.

## Evidence

`tests/test_graph_sync.py` — 3 passed (create-then-delta parity including
importer relink without reparse; broken-delta fallback restores parity with
the published disk state and logs it; delete removes the file and its
edges). `tests/test_p06_journey.py` — 1 passed. Neighbours
(task/task_execution/task_changeset/internal_replan) — 52 passed together;
ruff and compileall clean.

## Boundaries

The graph store is an advisory structural index: nothing reads it for
authority, read-proof or scope decisions (ADR-0001 §contracts). The §7.7
journey still misses the failure→authorized-repair leg in one test (next
package); legacy tree rebuild in `_refresh_index` is unchanged and remains
the load-bearing index until the P02 debt chapter swaps readers over.
