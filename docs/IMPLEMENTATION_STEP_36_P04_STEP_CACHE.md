# Package 36: step-focused, revision-bound read-plan cache

25 October 2026. Starting Git HEAD remains
`20cdd7767ad846ad42dd9d08fcd709c4d5c11b0d`, with package 35 already present as
uncommitted working changes. This is another local package, not new CI evidence.
Live provider calls = 0. Existing dirty files and prior evidence were preserved.

## Changed behavior

`TaskRunner` selects read-plan goals from the current step description and
expected outcome, intersected with admitted Python files and exact approved
paths. Explicit file paths take precedence over lexical symbol/file matches.
At most four goal candidates are selected; a no-match produces an explicit
search notice instead of choosing the first files in the approval scope.
Goal entries precede advisory name-level callers/definitions/import dependencies.
The model-visible query still carries the notice separately from retrieval:
`retrieval_query` remains the original step query.

The context-pack contract is now `context-pack/2`. Ordinary import dependencies
of a goal, e.g. `app.py -> lib.py`, are included as advisory name matches; this
does not establish exact dynamic call resolution or complete re-export semantics.
Byte caps, displayed-entry limits and structural parse failures remain explicit.

## Cached structural projection

`graph/read_plan.py::ContextPackIndex` is an ephemeral read-planning projection,
not a second durable graph store. It holds revision-bound structural file facts,
symbol definitions and reverse-name buckets. An update hashes current bytes,
parses only changed/new files, removes deleted files and patches owned buckets.
It does not call `clean_build` or rebuild inferred edges for the whole project.

The cache is bound to task/workspace, exact approved scope, extractor version,
admission configuration and host resource limits. Content identity uses SHA-256,
not size/mtime. Same-size/mtime changes and rollback to old bytes produce the
corresponding new/restored revision. New tasks/scopes receive a separate index.

Updates prepare changed payloads and candidate indexes before publication. A
cancelled update or node-limit refusal leaves the prior index intact. The live
runner drops its cache on any scan/projection failure and propagates cancellation
before constructing an Orchestrator. It never turns cancellation into a warning
followed by model execution.

The runner exposes `read_plan_report` with revision, selected goals, parser work,
reuse/deletion counters, omitted candidates and parse failures. These diagnostics
are advisory; they do not grant source read proof, edits, execution or approval.

## Source admission before reading

The old `project_files` implementation used unbounded `rglob/read_bytes`.
The live scanner now uses the existing `source_files`, `_Ignore`, `admitted_path`
and identity-checked `stream_source` contracts. It applies private-path exclusion,
gitignore/configured ignores, source file/count/total-byte/depth/entry limits,
private-workspace handling and cancellation before bytes reach the projection.
`.gitignore` is bounded and checked before/after reconciliation; policy drift
refuses the snapshot. Unexpected admission/read failure refuses the whole plan,
rather than returning a partially updated projection.

This does not claim an atomic filesystem-wide snapshot against arbitrary external
editors or policy ABA changes. Existing tool-level content checks, source leases,
scope enforcement and final host verification remain mandatory.

## Verification

Final selected integration run: **100 passed / 1 skipped in 105.49 s**, spanning
cache/impact/admission, graph deltas/resolution, TaskRunner/changesets/execution,
internal replans and composition. A subsequent distinct task/scope-isolation case:
**1 passed in 1.98 s**. The JUnit case identities are combined without duplicates:
**101 passed / 1 skipped**. The skipped case requires OS symlink creation rights.

Earlier overlapping development selections (14, 21 and 23 passed) are not added.
The first live-test draft had two faulty assumptions: it rejected legitimate
same-name advisory definitions and created the staged workspace inside the
original approved source root, correctly triggering source drift. The revised
tests assert goal priority and use a separate staged workspace.

Meaningful checks include:

- Unchanged snapshot: zero repeated AST adapter calls.
- One same-byte-count body edit: one parse; rollback restores the old revision.
- Add/delete/move/symbol updates: canonical pack parity with a fresh projection.
- Cancellation and node-budget refusal do not publish partial state.
- Node growth offset by another file shrinking fits the final snapshot budget.
- Relevant `z_target.py` remains first with more than twelve approved files.
- Ignored, hidden and credential-shaped paths are not opened.
- Source byte-limit and mid-scan ignore-policy changes refuse the snapshot.
- Cancelled scanning never starts the Orchestrator.
- Narrowed scope and a new task cannot inherit the previous goal cache.

Ruff, compileall, local link validation and `git diff --check` PASS.
Logs, JUnit and source-bound manifest: `validation/p04-step-cache-20261025/`.

## Remaining work

Reconciliation still traverses and reads/hashes admitted source bytes at the
read-plan boundary. The change removes repeated AST extraction and whole-graph
construction from this planning projection; it does not establish constant-time
freshness or measured repository p95/RSS improvements.

TaskRunner/Orchestrator still use the legacy source-index refresh path elsewhere.
The durable graph/delta store has not replaced every retrieval/tool hot path.
The snapshot-to-projection API itself is advisory and assumes the caller has
performed admission; only the live scanner supplies those access checks.

Next work: a durable task frontier with verification/evidence links (P05), then
the accepted graph/delta-to-legacy-index bridge and remaining resolver cases.
Full P02/P04/P05/P06, live utility and all four production blocks remain OPEN.
