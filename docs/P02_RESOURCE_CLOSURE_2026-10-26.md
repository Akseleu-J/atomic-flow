# P02 resource and propagation acceptance addendum

This supplements `P02_CLOSURE_2026-10-25.md`. It does not replace the original
P02 exit criteria or mark pending verification as accepted.

## Additional integration gaps addressed

External reconciliation and initial/recovery live graph synchronization now
admit the same Python/configuration/document/pipeline owners as the graph
adapters. Python-only rejection is removed. Files outside the supported adapter
admission contract are not claimed as analyzed graph owners. Watch events remain
hints; reconciliation audits admitted source contents to recover missed events.

Delta invalidation propagates the actual affected resolution closure, including
transitive re-export consumers. Batch reports union those paths rather than
reporting only supplied mutation paths. Export comparison includes static
wildcard visibility and dynamic-export status, so an `__all__` change can
invalidate consumers even when declared symbol IDs are unchanged. The resolver
still computes export closure over stored facts; this is not a claim of
constant-time metadata work or of executing/importing repository code.

## New acceptance cases

`tests/test_p02_resource_closure.py` adds:

1. Mixed Python/config/document/pipeline batch parity, followed by non-Git
   external discovery of a resource add/delete and pipeline change.
2. Static wildcard visibility changes, dynamic exports, and recovery to static
   exports: clean-build parity, transitive consumer invalidation, and no
   invalidation of an unrelated file.
3. Provider move/delete in a batch: clean-build parity and full invalidation
   closure through a re-export bridge.
4. Live graph sync initial admission and owned document-edit parity in a mixed
   project.
5. Cancellation after a private graph delta in the public synchronization path:
   persisted bytes and generation stay unchanged, followed by successful
   recovery to clean-build parity.
6. A provider body edit retains existing incoming calls when symbol IDs survive,
   without unnecessarily relinking its unchanged consumer.
7. Three import-shadowing cases refuse invented calls to the original imported
   provider after a local assignment, function definition, or module alias
   reassignment.

The integrated focused selection completed with **25 passed, 0 skipped** in
67.63 seconds. Evidence: `validation/p02-closure-20261025/final-integration.xml`.
It includes the six original new P02 cases, nine cases in this addendum, and
ingestion/live synchronization neighbors. Earlier evidence (46 passing neighbors
and one failed exact-dictionary assertion) is retained as a superseded failing
run, not presented as all-green acceptance. A memory-starved PowerShell run was
interrupted and supplies no pass evidence.

## Original P02 criteria and evidence boundaries

| Original requirement | Implementation and evidence | Gate status |
| --- | --- | --- |
| Changed/add/delete source deltas with old-source and graph-generation preconditions | `file_delta`/`apply_delta`; `apply_changes` stages a batch and performs one final CAS. Delta/CAS neighbors and focused parity journeys exercise this path. | Implemented; focused selection passed. |
| Git, watcher, and non-Git external reconciliation; events are hints | `ingest_source_changes` audits admitted source bytes; watcher reconciliation acknowledges only the current source event. Dropped-event, add/delete, and mixed resource reconciliation have clean-build parity cases. | Implemented; focused selection passed. |
| Parse only changed files on the ordinary owned-edit path | Exact `changed_paths` tree refresh, cached source facts, and selective relinking. Private body-edit tests count one parsed/read file and retain incoming call edges without consumer relinking. | Implemented; focused selection passed. |
| Dependency propagation includes unresolved references, aliases, re-exports, wildcard consumers, and deletion | Stored binding/call/export facts and pre/post dependency closure include modules that did not previously resolve. Static exports converge to a fixed point; ambiguous/dynamic targets remain unresolved. Import-shadowing cases refuse invented provider calls. | Implemented; focused selection passed. This is conservative static resolution, not arbitrary Python execution. |
| Changes reach the full affected resolution closure | Export IDs, wildcard visibility, and dynamic status trigger transitive consumer relinking. Reports expose the union of affected paths; unrelated files are excluded by regression assertions. | Implemented; focused selection passed. Metadata export closure still scans stored facts. |
| Retrieval/index propagation and global algorithm invalidation | Tree delta refresh updates edges/fan-in. `ActivationIndex.updated` reuses unchanged lexical features and rebuilds exact postings/adjacency; PageRank invalidation is explicit. Ranking-equivalence and reuse counters are tested. | Implemented; initial six-case selection passed; broader integrated neighbors remain separately tracked. |
| Transactional staging, rollback/cancellation, and source-bound publication | Batch deltas stay private until the final CAS. Private-stage and public live-sync cancellation preserve persisted bytes and generation, then recover to clean parity. | Implemented; focused selection passed. Actual process-crash/OS acceptance remains a distinct integration evidence gate. |
| Move/rename accounting preserves path identity without silently merging IDs | Delete/add moves obtain clean-build parity and invalidate the full consumer closure; IDs follow the new path. The current `changed` dictionary does not retain an explicit old-to-new mapping. | **Pending explicit mapping contract if required by the original move acceptance clause; delete/add parity alone does not close that clause.** |
| Reasoned recovery for schema/parser/resolver/ignore/watch/branch drift | External reconciliation provides a full content/parity audit; tree ignore-policy mismatch has an explicit fallback. Parser/resolver cache-version drift requires an additional preflight guard under integration review. | **Pending cache-version guard and its regression evidence.** |

This chapter is not declared completely accepted while the pending rows remain.
Final acceptance also requires the source-hash seal and integrated CI evidence.
No paid inference or external project tests were invoked for this work.
