# Step 22: bounded source snapshots and measured resource smoke

24 October 2026. This closes the unbounded whole-project snapshot traversal/copy
portion of W7. It does not establish unrestricted large-repository production
acceptance or change model task quality.

## Implementation

`ProjectLimits` is trusted host configuration. The defaults admit at most 10,000
source files, 128 MiB source bytes in total, 16 MiB per file, 100,000 traversal
entries and 64 directory levels. Chunked hashing/copying uses 256 KiB blocks.
The existing index node ceiling remains 100,000. Limits must be positive integers.

`ChangeSet.create(..., limits=..., cancel_check=...)` applies the envelope to every
admitted file, including non-indexed source assets. The snapshot cannot silently
accept truncated coverage. Ignored/private credentials remain excluded. Source
links/junctions remain refused through confined path checks. Traversal checks
cancellation per entry; hashing/copy checks it per chunk and at file boundaries.
Copying uses a hash compared with the baseline and file identity/size/mtime checks
around each read, then the original whole manifest is checked again. Cooperative
source publication locks and final content freshness checks are preserved.

The resource envelope is retained in the changeset journal and restored on reopen;
original/workspace validation and publication use it. Checkpoints also use bounded
streaming copies and total disk admission. Publication/recovery file buffers are
bounded by the per-file ceiling. Recovery intentionally completes without honoring
task cancellation; a resource/conflict failure preserves its recovery-conflict hold.

Before allocation, disk admission estimates source copy bytes, metadata headroom
and a 64 MiB free-space floor; private Git snapshots estimate extra object storage.
The free-space floor is checked again before each copied file. A cancellation or
failure during copying removes the partial current file and retains a
`creation_failed` journal plus any earlier completed files for explicit inspection.
Copy mode and private git-worktree mode retain their original ownership checks.

## Evidence and limits

Focused regressions cover count/total/per-file/depth/entry admission, secret
exclusion, disk refusal, chunked I/O, persisted limits, interrupted copy cleanup,
source conflict and checkpoint validation. Existing changeset, source serialization
and private worktree tests are included in the scoped check. Exact run results are
recorded in `validation/import-20261024/resources-scoped.txt`: **38 passed /
1 skipped in 312.39 seconds**. This includes the private Git worktree regressions;
the skip is not counted as acceptance.
No paid model calls, Docker or full-suite run is part of this package.

`tools/resource_scale_smoke.py --work-root <new-directory> --output <report.json>`
creates only 16/64/256 synthetic source files and matching journal append counts.
It reports snapshot duration, cancellation checks, Python allocation peak, current
RSS when available, journal append latency and measured journal read bytes. The
work root is explicit, must have fresh case subdirectories and is retained. This is
a bounded measurement on this host, not a benchmark against competitors or a
stress test of production ceilings.

Measured Windows smoke (`resources-scale-smoke.json`, synthetic, model_calls=0):

| Files/events | Source bytes | Snapshot seconds | Python allocation peak bytes | Journal bytes reread |
|---|---:|---:|---:|---:|
| 16 | 65,632 | 4.51 | 338,804 | 37,117 |
| 64 | 262,528 | 12.15 | 346,457 | 564,262 |
| 256 | 1,050,112 | 73.25 | 423,436 | 8,903,950 |

RSS snapshots were 26.9/18.2/6.1 MB; paging and shared-host load make these unsuitable
as stable peak-memory claims. Journal append median latency was 33.6/36.3/111.2 ms.
Latency includes flush and host filesystem contention. The 4x event increases
produced approximately 15-16x reread-byte work, consistent with retained quadratic
prefix validation. The bounded source smoke does not measure production ceilings.

The 38-pass run predates the additional Git inventory output cap. A follow-up
focused check exercises that cap, its early-kill path, refusal journaling and
normal private worktree behavior; its separate evidence is
`resources-git-cap-recheck.txt`. The scale smoke used copy mode and is unaffected
by the Git inventory follow-up. Final follow-up: **23 passed / 1 skipped in
40.11 seconds**, and targeted Ruff PASS (`resources-final-ruff.txt`). The skip is
the existing symlink-creation case unavailable on this Windows host.

The journal still hashes the committed prefix before append so same-size/mtime
tampering and cross-process integrity are detected. Consequently repeated append
still performs quadratic total prefix-byte work. This package measures that cost;
it does not claim to eliminate it. A transactional/segmented journal requires its
own integrity, crash-recovery, compatibility and cross-process acceptance work.

Disk admission is an estimate, not an OS reservation; concurrent disk users can
consume space after checking. Publication still buffers one admitted file to use
the existing checked replace interface. Git plumbing can wait up to its existing
30-second timeout between cancellation checkpoints. Original Git index inventory
output is bounded by the smaller of 4 MiB and the configured total source limit;
an oversized inventory kills the child and refuses creation. Other Git plumbing
returns fixed-size object IDs or bounded metadata. New default file ceilings can
refuse an oversized historical workspace until an explicitly reviewed host limit
is used; no partial publication is allowed to bypass the limits.
The envelope bounds source/Python buffers and inventory output, not the Git
child's OS memory usage; an OS memory quota for trusted Git is not implemented.

## Journal follow-up: preserve integrity, bound copies, retain the append gate

Investigation confirmed that a portable shortcut based only on file size/mtime,
the last record, or a sidecar head digest cannot prove that an earlier byte in a
mutable JSONL prefix has not changed. Segments marked read-only by the same user
do not supply an immutable storage boundary. A SQLite or segmented redesign alone
would move, rather than preserve, the current old-byte tamper-before-append check.
No such shortcut or implicit format migration was introduced.

The existing 256 KiB event and 64 MiB session caps are retained. A new event-count
cap was deliberately not added: without reserving observation space before a
provider/tool request, it could deny the terminal/unknown-effect record after the
external operation had already occurred. Byte admission is finite, but it is not
a journal latency SLO or a guarantee of post-effect observation capacity.

The accepted narrow improvement streams `read()` with at most `MAX_EVENT_BYTES+1`
bytes per input block instead of allocating the whole file, a second prefix slice,
and split-line copies. A malformed unterminated tail remains uncommitted even when
it exceeds the per-event cap; a newline-terminated oversized event still fails
closed. The total byte cap is rechecked while reading, preventing concurrent file
growth from bypassing the initial stat check. The returned decoded snapshot still
contains the complete event list and consumes memory proportional to that list.

`recover_tail()` performs the full streaming validation once, then seeks directly
to the uncommitted tail and streams only those bytes to the fsynced backup. It no
longer rereads/copies the entire file to extract the tail. Truncation and recovery
observation remain explicit and leased; pending/unknown calls are never replayed.
The validated prefix fingerprint is updated with the recovery event, so the next
append still verifies all prior bytes. Old same-size tampering, restored mtime,
foreign writer extensions, expected-sequence conflicts and process leases retain
their existing checks.

Focused final evidence: `session-streaming-final-with-counters.txt` **28 passed /
4 deselected in 7.01 seconds**, `session-streaming-final-with-counters-ruff.txt`
PASS. An instrumented regression confirms exact recovery reads equal one full
validation plus one tail-only backup copy. The earlier focused run had 27 passed
before this additional counter test. The initial run had 26 passed and one new
test expecting only `interrupted tail`; the existing append path correctly reports
`size limit` for a huge unterminated tail. Only the fixture expectation was widened;
the raw initial log is retained. The excluded tests concern job GC/workers, which
are outside this change. A small synthetic remeasurement is retained separately
in `session-streaming-small-scale.json`; it explicitly reports that quadratic
append work is **not fixed**. Its 16-event journal still reread **37,240 bytes**;
the snapshot took 0.570 s with a 339,580-byte Python allocation peak. Host load
differs from the earlier run, so these timings are not a claimed speedup.

A future canonical-store design needs explicit versioning and crash-safe legacy
migration, append transactions with sequence/head conflict checks, a persisted
digest chain, bounded reads, durable tail/uncertainty handling, and an integrity
boundary that detects old-page changes without silently trusting mutable cached
bytes. Acceptance must include arbitrary same-size old-record changes with restored
mtime, prefix truncation/replacement, corrupted metadata, concurrent writers,
crashes around each commit phase, reconciliation, and reserved capacity for terminal
observations. Until these pass under a stated storage threat contract, full-prefix
rehashing remains the portable append behavior and its quadratic total byte work
remains an open production scaling barrier.
