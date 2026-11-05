# af-evidence v1

Core-only verification data contract and separate trusted host receipts. No CLI,
HTTP, browser, provider, process or filesystem execution dependency is imported.

`VerificationReport::not_assessed(binding)` starts every check with zero checked
and denominator zero. Identifier hygiene, exact citation validity, evidence
coverage, syntax, tests, requirements, advisory model critique and publication
are separate fields. `CheckSummary` accepts `passed` only for a complete nonzero
denominator; incomplete coverage must remain visible. A known identifier is not
behavioral correctness. No score is a confidence probability.

`parse_report` parses directly into strict serde structs, not an intermediate
JSON Value. Unknown/duplicate fields, boolean/fractional/overflowing integers,
nonfinite JSON numbers, unsupported versions and trailing data are rejected.
Wire limit is 256 KiB, each list is limited to 256 entries, counters to 1,000,000,
quotes to 16 KiB, source snapshots to 16 MiB. `report_json` emits compact,
deterministic declared-field order; this is the v1 canonical ordering, not JCS.
A future schema needs an explicit migration; unknown versions fail closed.

`DeliveredSource::from_host` must be called by the trusted materializer only
AFTER policy/source validation and actual model delivery. It is not deserializable.
It records the exact delivered byte range plus the complete current-source hash.
`validate_citation` checks node/source identity, exact current revision/hash,
quote SHA-256, offsets and delivered bytes. Partial delivery remains partial;
valid quotation proves provenance, not relevance or logical entailment.
Claims default to `not_assessed`. A data-only entailment label requires an explicit
independent-checker identifier; that identifier is not proof that a checker ran.

`HostRequirementChecker` is the host integration port. Implementations must run
an independent pinned requirement oracle (or obtain explicit human review) and
return the actual complete observation, including checked/passed/failed/skipped
counts and an artifact digest, after verifying source freshness. `run_requirement_check`
mints an opaque `HostRequirementReceipt` only for a complete nonzero scoped
behavioral/human check. Syntax-only, model critique and generic process exit zero
are refused. `UnavailableChecker` always refuses; no built-in adapter pretends to
execute a requirement checker. Trusted Rust callbacks are not sandboxed.

`completion_decision(current_scopes, receipts)` requires exactly one receipt per
current requirement, bound to task ID, requirement-contract digest, checker ID,
workspace, source revision, source inventory, runtime, independent check inventory
and expected nonzero count. All current requirements must use the same task and
source binding. Host checker IDs are configured labels, not cryptographic identities. Empty obligations
are `not_assessed`. Receipts have no Deserialize/public constructor, so browser
JSON/report fields cannot mint them. The decision confirms those requirements
only. It grants no permission, source-read proof, publication approval, mutation
or replay capability. Host policy, current-source freshness, cancellation,
unknown-effects and approved publication gates remain separate responsibilities.

Frozen cross-language vectors are `fixtures/wire-v1.json`; each case contains an
exact JSON string and expected validity. Export with
`cargo run -p af-evidence --example export_vectors` from the Rust workspace.
The fixture includes nested duplicate keys intentionally. Clients must validate
the raw JSON stream to preserve duplicate detection before constructing objects.

Limits: no general semantic entailment engine, behavioral execution adapter,
model-quality evaluation, policy engine, storage or migration adapter is included.
Unit fixtures are protocol checks, not independent production checker evidence.
