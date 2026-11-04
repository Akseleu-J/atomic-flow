# Production status and next packages — 4 November 2026

Decision: **NOT READY for production**. All 24 full chapter gates remain open. P07 bounded engineering is accepted separately; historical Python P05 functional-core acceptance retains its exact source/runtime. Native Studio is an accepted engineering increment. Its safe apply UI remains disabled pending an admitted requirement checker and review/publication contract.

Snapshot: `8eff6f25beafb712c89aa1749a039c11f44ec3ae`. The [machine-readable registry](PRODUCTION_STATUS_2026-11-04.json) contains implementation paths, obligations, dependencies and evidence. This inventory is not a runtime grant or a new chapter acceptance decision.

The following source audit describes the starting snapshot. The subsequent
[E00/E01 + P09/E04 increment](PRODUCTION_INCREMENT_2026-11-04.md) adds the
shared evidence contract, recovery and SDK migration. Its validation is recorded
separately; none of the full chapter gates changes automatically.

## Authority and accepted evidence

Latest owner Rust/TS reconstruction instruction supersedes historical Python-only product scope. Chapter numbers and unmet safety/evidence obligations survive.

Rust owns runtime authority; TypeScript owns UI/client/language adapters; Python remains reference/oracle/lab/compatibility.
One mutable task/session has one owner. Historical read proof/approval and unresolved paid holds do not migrate implicitly. Corporate/distributed/hostile multi-tenant deployment remains unsupported. No extra language is added for variety.

- Native CI **41842587975**, tested `7279e237358e96ed32583aca3f2931697937731c`: Linux **292 PASS / 1 IGNORED**, Windows **290 PASS / 1 IGNORED**; native HTTP smoke and **8 UI tests** each. The ignored test is real Docker acceptance. Both archive digests were rechecked here; retained audits record 84 exact Git-byte source matches each, without repeating that raw-source audit. [Studio acceptance](STUDIO_ACCEPTANCE_2026-11-04.md), [Linux audit](../validation/zcode-studio-20261104/ci-7279e23/audit-linux.json), [Windows audit](../validation/zcode-studio-20261104/ci-7279e23/audit-windows.json).
- P07 engineering on `f76a59e`: CI **41655855886**, artifact **13239936458**, SHA256 `fbb8c466f62eb844ff13a1cb0dbc4940535087ae3094a03d4bbc4839b05a2cb4`; 135 retained raw hashes, six complete/cleaned checks, FAIL → one replan → repair → fresh PASS → publication. Authority is **Python TaskRunner**, with native Rust/TS target checks. This is not native TaskRunner cutover or measured utility. [Engineering acceptance](P07_POLYGLOT_TASKRUNNER_2026-11-03.md).
- Python P05 functional core acceptance is scoped to `91d22e2d1ed3e4d415bbb72076503cc4c6bf2fe3`; native parity and full chapter acceptance remain separate. [Historical context](PROJECT_CONTEXT.md).
- **provider_calls=0** for native/P07 engineering evidence and this pass. Research pairs, replacement holdout, ablations and historical UNKNOWN remain open. No new generation or independent answer grade was performed.

## Chapter map

Every full chapter below is **production OPEN**. Present code is a candidate implementation, not proof of parity or cutover. The JSON names all gaps and historical reference scopes.

| Chapter | Current implementation / retained engineering evidence | Next package |
|---|---|---|
| P00 — Frozen baseline and acceptance contracts | Scoped registry helper and source-bound native audit exist; this inventory is not acceptance. | N01 |
| P01 — Graph schema, adapters and migration | Schema/store/canonical identity and reference fixtures exist. | N04 |
| P02 — Delta structural updates | File delta/relocation/resolver/store code exists. | N04 |
| P03 — Incremental semantic graph and UA operations | semantic.rs supplies deterministic path layers/counts/summaries, not LLM semantic enrichment. | N05 |
| P04 — Evidence-aware context and editing | Source-bound context, scoped CAS edits and diagnostics exist; generic exit zero stays not_assessed. | N03 |
| P05 — Hierarchical DAG and rolling planning | DAG frontier, step invalidation and bounded repair exist. | N06 |
| P06 — Bounded autonomous execution and replan | Loops bind scope/cancel/effects uncertainty; local journey integrity cannot verify requirements. | N06 |
| P07 — Engineering autonomy and separate measured utility | Bounded mixed Rust/TS engineering pilot accepted on f76a59e through actual Python TaskRunner, six completed/cleaned checks and publication. | N06 |
| P08 — End-to-end performance and efficiency | Sample statistics and scratch two-file delta/clean helper exist. | N07 |
| P09 — Long sessions, recovery and operations | Journal/segments and durable intent/backups/root uncertainty hold exist. | N02 |
| P10 — Isolated worker and live routes | Docker profile/host provider transport exist. Historical P07 image has its own accepted Linux Docker scope. | N08 |
| P11 — Reproducible native release and consumer | Locks, artifact-hash/consumer comparisons and upgrade-record helpers exist. | N09 |
| P12 — Single-owner production RC and beta | RcRegistry models scoped records/not_ready; no actual production decision established. | N10 |
| P13 — Studio, language and integration parity | Served Studio graph/source/chat/history/snapshot/provider/budget surfaces and TS sidecar/client exist; no ts/af-studio React app. | N03 |
| P14 — Measured differentiation | Positioning hypothesis and paired-result machinery exist. | N12 |
| E00 — Core ports and modularity | Crates separate graph/context/journal/budget/LLM/execution/sandbox; shared claims contract is next work. | N01 |
| E01 — Honest evidence and verification | Unchecked requirements/context stay not_assessed. Model events cannot create authoritative success. | N01 |
| E02 — Retrieval completeness and uncertainty | Lexical/graph context and omission-bearing envelope exist; Rust syntax-only omissions explicit. | N05 |
| E03 — Principal and resource authorization | Root grants/HTTP token/Origin/Host controls plus PrincipalScope namespace and RBAC helpers exist. | N11 |
| E04 — Durable tasks, admission and scheduling | AdmissionQueue models bounded entries/fences in memory; jobs/journal/bounded stream exist. | N02 |
| E05 — Data governance and project isolation | SHA256 source identity/redaction/root grants/host provider configuration and egress matrix helper exist. | N11 |
| E06 — Provider, extension and central budget contracts | Durable local reservation/send/settle/HOLD ledger and typed provider outcomes; scripted conformance/no-replay helpers. | N08 |
| E07 — Operations, release security and scale | In-memory audit/OTel-shaped records, measurement and artifact helpers exist. | N07 |
| E08 — Headless corporate-core release | PrincipalAcceptance emits distinct namespace record and explicitly leaves other corporate gates separate. | N11 |

## Ordered packages with reviewable exits

These are scoped increments. Completing one does not rename a full chapter gate. Safe reconstruction proceeds after the accepted P07 pilot; the historical research campaign is a separate claim gate. Instrumentation and safety hardening may proceed early without claiming later chapter acceptance.

### N01. Freeze native claims and evidence contracts

Chapters: P00, E00, E01. Dependencies: none. Status: planned, not accepted.

Shared bounded VerificationReport/Claim/Citation identities, not_assessed/omissions, compatibility/malformed vectors and claims audit; contract package does not admit behavioral success.

### N02. Publication recovery and durable task boundary

Chapters: P09, E04. Dependencies: N01. Status: planned, not accepted.

Exact before-byte backups/intent/root-hold recovery with error/conflict/crash fixtures; persistent authenticated idempotency/reserve/outbox/task boundary. Scope slices; no fabricated cross-store atomicity.

### N03. Requirement checker and review/apply contract

Chapters: P04, P13, E01. Dependencies: N01, N02. Status: planned, not accepted.

Host checker rejects forged/stale/incomplete/zero-test/required-skip receipt; exact reviewed source/replacements; UI apply only after fresh final and recoverable publication.

### N04. Graph delta and language adapter cutovers

Chapters: P01, P02, E02. Dependencies: N01. Status: planned, not accepted.

Reference/native canonical fixtures, source-bound mixed language analysis, honest omissions, delta-clean equality, conflict/cancel/migration cutover receipts.

### N05. Semantic understanding and complete context

Chapters: P03, P04, E02. Dependencies: N03, N04. Status: planned, not accepted.

Dependency/revision-bound enrichment/tours/impact, scoped caches, explicit completeness and pinned negative/no-answer evidence.

### N06. Native TaskRunner vertical slice

Chapters: P05, P06, P07. Dependencies: N02, N03, N05. Status: planned, not accepted.

Public native understand/plan/edit/complete FAIL/one repair/fresh checks/publication/recovery, mixed Rust/TS, shared budget/cancel/lineage and negatives. Retain accepted Python TaskRunner pilot; no endless old P07 campaign as reconstruction blocker.

### N07. Actual operating envelope and fault acceptance

Chapters: P08, P09, E07. Dependencies: N06. Status: planned, not accepted.

Pinned repeated phase/RSS/disk/cancel and soak/fault reports, real parity oracle, OS/process matrix and integrity-preserving journal migration. Efficiency claim waits for quality.

### N08. Native worker and authorized provider routes

Chapters: P10, E06. Dependencies: N02, N03. Status: planned, not accepted.

Separate-client Linux worker identity/resource/network/cleanup/reconcile evidence plus separately approved-budget route conformance. Isolation precedes all untrusted candidate execution.

### N09. Reproducible artifacts and installed consumers

Chapters: P11, E00, E07. Dependencies: N06, N07, N08. Status: planned, not accepted.

Locks/transitive hashes/SBOM/licenses/provenance, installed CLI/API/SDK consumer outside checkout and migration/rollback/restore evidence.

### N10. Scope-bound single-owner RC decision

Chapters: P12. Dependencies: N09. Status: planned, not accepted.

W1-W9/required cutover evidence, independent review, bounded beta observation/stop thresholds and owner acceptance.

### N11. Corporate headless profile

Chapters: E03, E04, E05, E06, E07, E08. Dependencies: N01, N02, N08, N09, N10. Status: planned, not accepted.

Trusted principal/resource enforcement/revoke, durable fairness/quota/server accounting/data boundaries, independent two-principal installed consumer and signed decision. Design can begin earlier; readiness unsupported.

### N12. Separately authorized utility and holdout

Chapters: P07, P14. Dependencies: N06, N08. Status: planned, not accepted.

Calibrated pairs/replacement holdout/ablations under frozen caps with independent grades and full cost/failure/UNKNOWN; live routes require explicit run budget. Engineering tests imply no superiority.

## Material gaps and review boundaries

- Native diagnostic exit zero remains `not_assessed`. A completed host-owned requirement receipt is required before behavioral success or automatic publication.
- `semantic.rs` contains deterministic counts/layers, not model enrichment. Rust macro/cfg/type/call resolution and full TS project semantics remain incomplete.
- `perf.rs` uses a two-file scratch workload; `parity_checks` increments without canonical clean/delta equality. It provides no accepted real-repository speedup.
- `AdmissionQueue` stores entries in memory despite its Durable submission comment. Principal namespaces, RBAC/egress matrices, distinct-namespace acceptance, RC/artifact fixtures and in-memory audit helpers are contracts, not durable scheduling, authenticated ACL, corporate acceptance or installed release evidence.
- TS SDK unchecked response casts and legacy provider configuration requests need migration. Browser visual QA and strict Windows Clippy remain unaccepted; native real Docker and live quality are separate gates.

Read-only source/retained evidence audit; no heavy tests, runtime edits, provider calls, commit or push. Rust/TS/workflow source at reviewed HEAD had no diff from accepted native CI source. Concurrent claims/recovery work is deliberately not accepted by this snapshot and requires fresh source-bound evidence. Historical archives and rejected attempts remain intact.

Source plans: [P00–P14](PRODUCTION_ROADMAP.md), [E00–E08](ENTERPRISE_CORE_PLAN.md), [W1–W9](PRODUCTION_PLAN.md), [Rust/TS reconstruction](POLYGLOT_RECONSTRUCTION_PLAN_2026-11-03.md).
