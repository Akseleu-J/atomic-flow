# P09/E04 bounded native publication recovery

Implemented in `rust/af-sandbox/src/recovery.rs` with trusted-host APIs:

- `SandboxRegistry::inspect_recovery(root_id, changeset_id)` performs read-only inspection and returns a `preview_digest` and exact review data.
- `SandboxRegistry::recover(root_id, changeset_id, preview_digest, host_approved)` requires the host's explicit approval bound to the freshly inspected digest. Neither model output nor a browser-provided boolean is authority.

Recovery restores original local source bytes only. It never replays tools, model/provider calls, spending, session continuation or external effects, and never changes budget/session unknown flags. Returned requirement status remains `not_assessed`.

## Bounds and validation

Input contains registered IDs, not arbitrary filesystem paths. The held publication must belong to the requested workspace/changeset and hash the exact immutable intent. New intents contain the exact root grant including generation and root identity. Legacy intents lacking that grant fail closed and require a separately reviewed host migration; no authority is reconstructed from them.

Recursive duplicate JSON keys (including equivalent escaped spellings), strict field sets, source/backup hashes, backup names, original SHA1 snapshot entries, full current filtered snapshot, current before/after SHA256 states, root identity, exact write scope and optional original terminal journal are checked. Link/reparse components, Unix hard links, unknown source bytes, foreign grants, malformed schema and bounded-size violations are refused. Source and backup reads are capped at 1,000,000 bytes each, with a 4,000,000-byte recovery aggregate, at most 32 changed files, 2,000,000-byte intent and 20,000-entry baseline. Existing snapshot traversal retains its entry/time/byte limits and existing ignore rules.

The digest binds intent, hold, exact grant, terminal if present, current hashes and current filtered manifest. Recovery revalidates after taking the publication lease and before each mutation, writes a durable recovery intent, restores only known before/after states via cooperating-writer compare-and-swap checks, verifies restored bytes, and persists per-file checkpoints and a completion report. Original intent/backups/terminal are preserved. Every failure retains the publication hold; post-unlink directory-sync failure attempts to restore the conservative hold and returns error. This restoration is best effort if the filesystem refuses all writes.

## Conservative overlap protection and limits

Publication now uses one exclusive lease and unresolved-publication gate per trusted state home. Any retained `.json` hold blocks publication through aliases, child roots, parent roots, sessions and other workspaces sharing that home. This deliberately serializes unrelated roots too. Inspection/recovery can address only the owning registered root and changeset; a different grant cannot clear it. Separate state homes, hostile filesystem modification and arbitrary noncooperating editors are outside this coordination scope.

This is trusted-host cooperating-writer protection, not a hostile-filesystem sandbox. Link checks and byte CAS do not provide a kernel-atomic compare-and-replace against arbitrary external editors. Hashes provide integrity comparisons, not an independent authenticity anchor. A malicious host that rewrites all records can forge this evidence. Failed storage acknowledgements remain unknown; the code does not claim filesystem power-loss durability.

## Validation

Final local Windows check: `cargo test -p af-sandbox --locked --offline -j 1 -- --test-threads=1` passed **26 tests, 0 failed, 0 ignored**, plus 0 doc-tests; compiler emitted no warnings. This includes the original 17 accepted sandbox tests and 9 added tests (one is the child helper; both process-kill parent tests independently ran it). Linux-only linked-backup coverage is present but has not been executed in this Windows run. No Linux/remote CI or full P09/E04 production acceptance is claimed.

Retained check history: the first compile failed on a new recovery-vector type inference error, corrected with an explicit type. One command invoked Cargo from the outer repository and failed immediately because no Cargo.toml existed there; it performed no build. The first completed test run was **22 PASS/3 FAIL**: manually seeded test holds used noncanonical Windows paths, while actual publication correctly used canonical grant paths; the parent-grant fixture also incorrectly enclosed the application state home. Both actual process-kill tests and all baseline tests already passed. Canonicalizing the manually seeded fixture and nesting source under a separate source parent fixed those fixture errors. The second run was **25 PASS/1 FAIL**, because the test placed parent and child grants in one registry, which correctly rejects overlapping grants. Moving them to separate workspaces exercised the actual cross-workspace concern and produced the final **26 PASS**. These were new test setup/compiler defects; baseline expectations were not weakened.

Added tests cover exact restoration/preserved original intent, approval/digest refusal, external-edit conflict, corrupt/oversize backups, invalid schema/paths/backup names, changed grants/manifests and overlapping grants in other workspaces. Unix additionally exercises a linked backup.

Two tests launch an owned test child running the actual `SandboxSession::apply`, wait for a test-only persisted boundary marker, forcibly kill and reap that child, inspect the retained publication, and explicitly recover it. Boundaries are after the durable intent/backups/root hold before the first source mutation, and after durable replacement before publication verification/terminal. This is actual process termination, not simulated power loss. Production builds expose no failure-injection environment hooks. No background service, network or provider call is used.

The host CLI is the sole recovery entry point in this increment; no HTTP recovery endpoint is added. Fault hooks compile only under `cfg(test)` and are absent from ordinary/release builds.

## Follow-up review: filtered paths and interrupted recovery

Read-only review of the host CLI confirmed that recovery is reachable only through the explicit digest-bound `--host-approved` command; these CLI commands address workspace `default`. The library supports other host-registered workspace IDs, but this CLI does not select them. No HTTP recovery route is added.

Review found a concrete availability defect in the first recovery implementation: the snapshot excludes ignored/hidden files, while exact host grants and publication can intentionally address them. Recovery wrongly required those original bytes to be present in the filtered snapshot and inserted ignored changed paths into the filtered current-manifest comparison. The fix verifies every explicitly granted changed path independently using its before/after SHA256 and exact backup, checks its SHA1 baseline entry whenever present, and compares full unchanged filtered snapshot coverage after removing changed paths from both manifests. It does not relax unchanged-file coverage or unknown-current-byte refusal. Added regression restores both `.hidden.py` and `package-lock.json`.

Added a third actual owned-child kill test: a two-file recovery is terminated after the first verified restoration and persisted recovery checkpoint. The parent verifies a-before/b-after state and the retained hold; the original review digest is refused, a fresh review digest authorizes only byte restoration, and a new recovery attempt restores both files while preserving the original intent and the interrupted attempt checkpoint. This tests recovery restart without action replay or a power-loss claim. These two new tests are pending the root agent's serial final Cargo run; the earlier 26-pass result predates this follow-up fix/tests.
