# P07: explicit oversized-file graph coverage

28 October 2026. Status: DESIGN ONLY; durable graph sync is not accepted.
The synthetic full-source native execution/publication acceptance is separate.

CI40637655114 on030d22f published only the admitted probe after native checks,
but task.graph_sync.failed=true. The final impact omission fix does not repair
the durable graph. Existing direct adapters refuse >1,000,000 bytes, while a
workspace admits up to16MiB/file. SymPy contains a1,508,403-byte ordinary test.
Do not raise the AST ceiling or silently omit this source from the graph.

## Required shared projection

Introduce one versioned project-file projection used by clean_build, file_delta
and incremental read planning. Small source uses the unchanged direct adapter.
Large but host-admitted source produces a file node with exact full-byte SHA256,
size, projection policy version, extractor graph-placeholder/1, omission reason
and extraction_status omitted_oversized. No symbols or extracted edges exist.
parse_ok is null. Compatibility imports/raw_calls lists are empty only with
explicit imports_status/calls_status/exports_status unknown. Unsupported paths,
non-bytes and source above the host admission envelope still refuse. Direct
AST/resource adapters remain strict and unchanged.

Resolver must preserve module existence without inferring known-empty exports,
imports or calls from placeholders. Emit explicit coverage omission in unresolved;
symbol and reexport targets remain unresolved. A shared coverage_from_file_nodes
must reconstruct sorted omissions from persisted facts after reload, including
clean/delta/fallback/sync reports. Sync distinguishes a successful graph commit
with incomplete extraction from an actual technical failure.

## Delta and downstream contracts

Small-to-large removes old symbols and owned/inferred edges; large-to-small
re-extracts. Add/edit/delete/rename retain content/generation CAS, source drift,
cancellation and crash atomicity. Clean and delta documents must match.
Explicit extractor validation recognizes placeholder and policy versions; do not
allow arbitrary unknown extractors. Cache/read-plan revision includes projection
policy version. A placeholder goal reports omission and coverage_complete=false,
never read proof. Impact stays unknown and full final tests remain mandatory.
Dependency closure is explicitly partial. Semantic admission still refuses a
snapshot above its existing cap; partial semantic enrichment needs its own
all-source digest/omission contract and must not be implied by this package.

## Required acceptance

Direct adapters still reject large files. Projection retains exact SHA/size and
unknown facts. Test small-large-small and large add/edit/delete/rename parity,
resolver import/reexport/call uncertainty, persisted reload/fallback omissions,
source/generation CAS, cancellation/crash, read-plan invalidation and final-test
authority. Then repeat the full native journey requiring both scoped publication
and successful durable graph sync with explicit incomplete extraction coverage.
This design was independently reviewed read-only; no implementation is claimed.

## Implementation checkpoint

Shared projection, durable clean/delta/fallback/sync coverage, read-plan policy
identity, semantic partial-coverage reports and host graph admission are now
implemented. Model read/edit keep their original1MB cap; host graph snapshots
use a separately validated bound up to16MiB without changing path/link/private
source rules. Peer review found unknown-star and opaque alias call-resolution
gaps; conservative unresolved handling and clean/delta regressions were added.
Root graph neighbors passed82 tests with1 platform-dependent symlink skip;
updated resolver/projection neighbors passed43 tests. Ruff passed.
Fresh Linux graph/native full-source matrix has not yet run, so graph runtime
acceptance is still pending. No paid inference was made.
