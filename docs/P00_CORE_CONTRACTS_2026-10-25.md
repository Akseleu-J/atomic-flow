# P00 — frozen contracts, claims and evidence design

Continued and rechecked on 26 October 2026; historical filenames retained.

25 October 2026. This is the required P00 design gate for E00/E01, not the full
enterprise implementation or release acceptance. Current code and historical
observations have separate source identities. `tools/p00_contracts.py` inventories
source hashes, dependency declarations/installed versions, module imports, named
test functions and immutable historical evidence bytes without executing fixtures.

## Core ports and dependency direction

| Port | Current owner/adapter | Stable contract and migration rule |
|---|---|---|
| GraphRepository | `graph/store.py`, `graph/compat.py` | Versioned validated snapshot, generation/content CAS; JSON backend now, storage change behind the port |
| SourceStore | `agents/source.py`, `changeset.py` | Exact revision/read-before-edit, admitted snapshots, conflict before publication |
| EvidenceStore | `agents/step_evidence.py`, completion reports | Source/contract-bound unsigned observation, no authority or semantic attestation |
| TaskRepository/Scheduler | `agents/task.py`, `frontier.py`, task loop | Durable finite lifecycle and one writer; no replay of completed/unknown effects |
| PolicyEngine | `agents/policy.py`, plan/envelope runtime gates | Host decision before effects; repository data cannot grant capabilities |
| BudgetService | `task_budget.py`, durable daily ledger | One shared physical-call reservation, settlement before cancel; unknown remains held |
| ModelAdapter | backend interfaces and lazy factories | Explicit endpoint/model/usage/outcome; timeout is not pre-inference refusal |
| ExecutionAdapter | `execution.py`, jobs/remote worker | Declared capabilities, bounded check, immutable launch identity and no unsafe fallback |
| EventSink | sessions/journal | Ordered durable data events; redaction and integrity, no executable replay |

CLI/API/LSP/MCP/browser/provider integrations compose ports and must not be
imported by graph schema/read/store. Existing interfaces are wrapped gradually;
this design does not authorize a broad rewrite. Dependency inventory records
static imports to check future changes; dynamic import and deployment acceptance
are separately tested by installed-wheel/runtime CI.

## Identity and state contract

Trusted host identities name principal, workspace, project, root, task/subtask,
attempt/call/job, source revision, policy/grant version and correlation IDs.
Graph record IDs are local to project/root; `GraphIdentity` binds their namespace
and `GraphReadContext` rejects different principal/project/root/policy or a missing
host root grant. The scoped store persists the identity, preventing caller relabeling of another
project/root snapshot. A caller-provided UUID is not a grant. The in-process no-context
GraphReader is explicitly trusted single-root compatibility, never a public API
authorization bypass. Corporate IdP/revocation/multi-tenant enforcement remains
E03/E05; the P00 requirement is the design and typed identity contract.

Task owns workflow; attempt owns a provider request; job owns execution; remote
owns transport; specialist owns advisory read analysis. Unknown call/effect is
not failed/not-sent and cannot authorize replay. Strict wire/state schemas reject
unknown fields, duplicate keys/IDs and nonfinite numbers. Changes to authority
need explicit versioned migration; legacy data is readable without restoring
execution rights. Internal replan envelope v2 retains absolute deadline while
legacy v1 requires fresh review.

## VerificationReport and omissions design

Versioned report dimensions are independent: identifier hygiene, citation validity,
evidence coverage, syntax, tests, requirements and model critique. Each measured
dimension carries status, checked denominator, passed/failed counts and nullable
score. `checked=0` means `not_assessed`/`not_applicable`, never perfect 1.0.
Identifier presence is lexical hygiene; it is not truth or entailment. Model
critique is advisory opinion and cannot assert independent verification.

Citation evidence names project/root, current source digest/node/span and exact
quote digest, with evidence that the span was actually delivered. Missing,
stale, forged, partial or irrelevant citations cannot pass coverage. Semantic
entailment remains separate from deterministic source/quote equality.

Retrieval omissions name reason (`stale`, `denied`, `unreadable`, `unsupported`,
`truncated`, `budget_excluded`), source scope and counters. Partial source is not
complete read proof. Unknown graph edges do not prove absence of impact; a
partial graph never authorizes skipping required checks. Candidate tests cannot
silently replace oracle identities or weaken required suite. Behavioral criteria
need independent tests or explicitly reviewed human evidence; otherwise
requirements remain `not_assessed` even if syntax/tests pass.

Public migration keeps deprecated legacy fields documented, adds report schema
version/capabilities and requires clients to preserve null/not_assessed. Current
P03/P04 integration fixes the confirmed `checked=0 → grounding=1.0` false assurance;
this design inventory never upgrades historical grading bytes retroactively.

## Claims and dependencies inventory

| Claim | Allowed evidence | Explicit limit |
|---|---|---|
| Host mechanics pass | Exact-source JUnit + source hashes + environment/command/exit | Scripted model does not measure LLM task utility |
| Local checker works | Trusted fixture execution and actual process output | Does not prove Docker/remote isolation |
| CI installed distribution works | Installed wheel smoke outside checkout per OS/Python | Not a deployment/consumer or reproducible release attestation |
| Graph understands supported syntax | Extracted/inferred provenance, spans and omission report | Unresolved/dynamic/unsupported are unknown, never absent |
| Semantic summary current | Source dependencies/model/prompt scope hash | Inferred explanation cannot issue read/edit grants |
| Baseline indicators | Historical cards with N, range, host/source identity | Single host/N=1 build is not p95/SLO or regression proof |
| Live task succeeded | Completed provider + downloaded patch + independent oracle | QUEUED/response/accounting alone are insufficient |
| Competitive improvement | Frozen paired model/tasks/caps and uncertainty | Historical SymPy pilot did not produce accepted atomic patch |
| Docker-free core | Headless installed client/core without Docker | Untrusted execution still requires accepted isolated worker |

Required runtime dependencies are pydantic/PyYAML/pathspec. API, encoder, Kaggle,
benchmark and dev dependencies are separate extras. Installed distribution inventory
is not lock/SBOM/provenance: those belong to P11. The existing package-data includes
benchmark fixtures; slimming distribution and measuring its savings belongs to E00
implementation, not a false completed release claim in this P00 design gate.

## Frozen acceptance/comparison contract

Every accepted observation identifies phase/source SHA and dirty paths, config
digest, environment/dependencies, command and exit, artifact hashes, failures/skips,
unknown charges, decision and reviewer. Local/CI/live/consumer are separate.
Historical baseline source/model/provider/dataset evidence stays historical.
`tools/p00_contracts.py` re-audits evidence digests without rewriting them.
The old acceptance registry's `ORACLE_PASS` live-pilot label is explicitly
corrected in the new inventory: it covered grader calibration, while the baseline
patch failed and Atomic had no accepted edit. It is never promoted to task success.

Primary exploratory hypothesis is preregistered before holdout: quality margin
5 percentage points plus at least 20% lower complete cost per verified success
against a capable plain-agent arm on 30 pinned tasks, frozen provider/model and
equal caps, alternating arm order. Record all failed attempts/interventions,
semantic indexing cost, tokens/calls, p50/p95 and stop reasons. A new confirmatory
holdout/sample-size calculation is required after exploratory results; exposed
SymPy case is development-only. P00 sends no paid calls and grants no run budget.
Unknown historical provider outcome/cost stays unknown, not zero or replayable.
