# Native production continuation — 5 November 2026

Continues the zcode `experimental` checkout from `7bf0661c973e46a80275c7755161286f449e037d`.
This integrates the pending native queue, requirement gate and SDK contract work;
the original P00–P14/E00–E08 chapter gates retain their separate acceptance.
This increment makes no provider/model calls and does not reset historical holds.

## N01 — matching evidence wire contracts

Rust now requires the explicit nullable `Claim.independent_checker` field, matching
TypeScript's exact object schema. Omitting the field is rejected, while explicit
`null` remains valid for an unassessed claim. A shared raw JSON negative vector
prevents this difference from recurring. Reports remain data, not execution or
publication authority. Quote provenance is not a proof of logical entailment.

## N02 — durable admission prototype

The local append-only queue now compares idempotent submissions under the same
writer lease as the append. Task, key, priority and payload digest must match;
conflicting identity reuse refuses without appending. A retry with the same
logical request returns the original entry. Reservation completion/failure must
match the stored worker and exact reservation epoch, including on ledger replay.
Counters use checked arithmetic, numeric fields are strict, total capacity is
checked before append, and uncertain persistence poisons the handle. Duplicate
JSON keys, extra record/creation fields, corrupt versions and torn tails refuse
without silently rewriting history.

This is a trusted-host prototype, not authenticated multi-user admission. Worker
labels and epochs do not authenticate a principal. It is not wired into an API
outbox, does not reclaim expired reservations, and does not reconcile unknown
external side effects. Queue entries have a finite capacity and no accepted
retention/compaction scheme. E04 remains open.

## N03 — requirement receipt freshness

The final edit gate binds the filtered source inventory plus SHA256 hashes of
exact edited paths, including hidden/ignored files. It checks the current grant,
canonical root identity and unresolved root effects before and after the host
checker, and before commit/completion. A changed source or grant invalidates the
receipt; checker execution is journaled before invocation, and freshness failure
retains durable UNKNOWN/Hold instead of committing or returning Completed.

Six negative callbacks exercise changes to the edited file, another source,
hidden and explicitly ignored edited paths, grant revocation and an unresolved
root hold. A supplied independent checker remains a trusted in-process adapter;
the positive fixture is scripted, not evidence that real requirements were met.
The HTTP route supplies no checker, so diagnostic exit zero remains unverified.
The checker API has no cancellation primitive; none is claimed here. Source CAS
checks do not isolate arbitrary hostile filesystem writers.

## Integration boundaries

Existing SDK additions validate health, session, budget and graph DTOs at runtime;
amounts remain exact nanodollar strings and unresolved budget holds stay explicit.
The pending clean/delta structural equality tests are exercised with the workspace.
They do not establish Rust/TypeScript semantic coverage or full graph cutover.
The React Studio work remains a separate working tree surface; it is not included
in this native acceptance or represented as a deployable release.

## Validation

- `af-evidence`: 13 local Rust tests passed, including the shared raw corpus.
- TypeScript 5.8.3 compile and client tests: 36 passed, no skips.
- `af-loop`: 45 passed, including all six freshness negatives.
- Initial eight freshness test failures exposed noncanonical Windows path identity;
  canonicalization fixed the implementation, without weakening its checks. The
  failed and successful logs are preserved in `validation/production-20261105/`.
- Combined workspace, installed native HTTP/SDK smoke and remote CI: pending.

The [24-chapter registry](PRODUCTION_STATUS_2026-11-04.md) remains `not_ready`.
Next gates: independent behavioral checker adapter and reviewed apply, actual
API idempotency/outbox/recovery integration, native TaskRunner cutover and its
mixed-language negative/repair/final-check evidence. Corporate identity, worker
isolation, utility/holdout, operations and release acceptance stay separate.
