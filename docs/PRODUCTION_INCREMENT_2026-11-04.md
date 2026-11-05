# Native production increment — E00/E01 and P09/E04

This is a bounded implementation increment, not acceptance of complete chapters.
The complete [24-chapter registry](PRODUCTION_STATUS_2026-11-04.md) remains
`not_ready`; original P07 engineering acceptance and research obligations retain
their distinct scopes. Provider calls in this increment: **0**.

## Evidence contract and API

`rust/af-evidence` defines a strict versioned report with separate identifier,
citation, coverage, syntax, tests, requirements, critique and publication fields.
Unknown fields, duplicate JSON keys, invalid counts/digests/spans and zero-check
`passed` are refused. A well-formed report is data only, never execution/read/
review/publication authority. Exact quote provenance does not imply entailment.

Host requirement receipts cannot be deserialized or constructed from HTTP JSON.
They bind task, requirement contract, checker, check inventory, source inventory,
workspace/revision/runtime and the nonzero expected checks. No behavioral checker
adapter is installed: `UnavailableChecker` refuses rather than inventing success.
Trusted Rust callbacks remain part of the host trust boundary.

`GET /v1/capabilities` describes this single-owner protocol. Completed chat
payloads and journal results include `verification_report` whose inventory hash
binds the exact delivered augmented prompt plus source metadata. Its revision is
explicitly a delivered-request snapshot, not an inferred Git commit. Coverage is
only `selected_request_excerpts`, all treated as partial; requirements and all
unexecuted check dimensions remain `not_assessed`. Selection exclusions and
stale/unreadable candidates are not a complete omission inventory in this slice.
Raw file hashes alone do not authorize citations against redacted excerpts.

The TypeScript client consumes the same frozen raw JSON vectors; raw duplicate
keys must be rejected before JavaScript objects lose that information. Runtime
validation and bounded transport are required even where TypeScript compiles.

## Explicit publication recovery

The native operator CLI exposes only registered IDs:

```text
af sandbox inspect-recovery --root-id ROOT --changeset-id CHANGESET
af sandbox recover-publication --root-id ROOT --changeset-id CHANGESET --preview-digest DIGEST --host-approved
```

Inspect the returned review and exact digest before invoking recovery. These
commands address the default workspace and the operator's selected
`ATOMIC_FLOW_HOME`; no browser recovery endpoint or model approval path exists.
They restore known before bytes, preserve original records, and write recovery
intent/checkpoints/completion. Current root grant, intent/hold hashes, backups,
full filtered manifest and per-file known before/after states must match.
External source conflicts refuse. Legacy intents without an exact grant refuse.

One publication lease and unresolved hold gate cover the whole state home,
including separate workspace namespaces and overlapping parent/child grants.
This deliberately serializes unrelated publications. Recovery never executes
models/tools, resets accounting/session UNKNOWNs or establishes requirement
success. The [recovery report](../validation/production-20261104/recovery-report.md)
records tests, rejected attempts and persistence/concurrency limits.

Protection assumes a trusted host and cooperating writers. Filesystem CAS checks
do not provide an OS-atomic transaction against arbitrary editors; separate state
homes are outside the shared lease. An unlink acknowledgement failure attempts
to restore HOLD and reports error, but recreation is best effort if storage
refuses every write. Actual process termination is tested; power-loss durability
and full cross-store task acceptance remain open.

## Release declarations and remaining work

The scoped RC helper now refuses empty, all-optional, duplicate and malformed
registries. Its output explicitly has `evidence_audited=false` and
`production_authorized=false`; declaration completeness cannot release a product.
The 24-chapter planning registry is validated for completeness, file/evidence
references and an acyclic next-package graph.

Next: host-owned independent behavioral checker and exact reviewed apply;
complete omissions inventory; persistent API task/idempotency boundary; native
TaskRunner cutover. Identity/resource ACL, distributed quotas, worker isolation,
installed signed release, operating envelope and quality/utility evidence remain
separate gates. Old accepted areas and historical paid holds are preserved.

## Validation record

Pending final combined local checks and fresh source-bound Linux/Windows CI.
No newer acceptance is inferred from the earlier `7279e23` Studio evidence.
