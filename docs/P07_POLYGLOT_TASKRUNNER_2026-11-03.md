# P07: bounded engineering exit and native pilot

**Current status, 4 November: engineering exit ACCEPTED on f76a59e.**
Native CI41655855886 SUCCESS; downloaded artifact13239936458 SHA256
fbb8c466f62eb844ff13a1cb0dbc4940535087ae3094a03d4bbc4839b05a2cb4.
All135rawhashes, six complete FAIL/PASS native receipts, source/final bindings,
two-file publication journal, supported graph projection and six cleanups were
independently rechecked. General CI41655869867 SUCCESS, source-equal485hashes
per Python3.11/3.12 suite (3594PASS/26SKIP each). Research quality remains open.
Implementation notes below retain the original preparation and rejected attempts;
their then-pending status is historical. Current Studio work is documented in
[the 4 November acceptance](STUDIO_ACCEPTANCE_2026-11-04.md).

Owner instruction, 3 November: finish necessary TaskRunner/P07 engineering,
then reconstruct on suitable languages. The pilot is Rust + TypeScript.
Historical paired research is retained as unfinished research, rather than
requiring every old environment investigation before reconstruction.

## Engineering acceptance

The new fixed `rust-typescript-v1` host obligation protects tests, Cargo.lock,
manifests and all other admitted files; only src/budget.rs and src/budget.ts
may change. Cargo and Node test output must contain the four frozen named
tests each. Compilation/setup errors, skips, filtered/zero tests, truncated
output and incomplete receipts never authorize repair or publication.

The executor uses an immutable local image assembled from digest-pinned
Rust/Python/Node parents and an integrity-pinned TypeScript archive. Target
code runs non-root without network, with a read-only admitted source copy,
bounded executable scratch (compiled Rust tests require execution), memory,
CPU, process, output and total time limits. Cleanup uses immutable IDs and
records observed absence. Raw streams are preserved separately from host
metadata. This is a trusted Docker host boundary, not host attestation.

Workflow `p07-polyglot-taskrunner.yml` runs the actual public TaskRunner:
completed native failure → one bounded replan → two-file repair → fresh
complete checks → final publication. It retains task, specs, raw receipts,
container inspections, cleanup observations and source/evidence hashes.
This is scripted lifecycle acceptance, with provider_calls=0. It establishes
neither live model quality nor a cross-language graph semantic oracle.

## Current gate

Implementation and focused local regressions are being checked. Real Linux
Docker CI and downloaded raw artifact review are REQUIRED before accepting
this engineering exit. P07 is not marked closed by this document.

First native journey on e7d0d93: CI41654592399 SUCCESS, six complete checks
(FAIL followed by five PASS), one replan, two-file publication, graph sync
for supported config projection. Downloaded artifact13239760175 SHA256
f24fef33da654b86b7d8293305645d23ea7fd6972ad0a39807151387d8b4b7be.
Archive audit rejected completeness: upload-artifact default omitted three
hidden files listed in hashes.json (workspace lock/tree metadata). No false
acceptance: first archive retained, workflow now explicitly includes hidden
files, requiring a new captured run. Local focused groups: 177 PASS and
48 PASS (native/integration group including failed-attempt evidence).

General CI41654606647 additionally found one regression (3575 PASS/26 SKIP):
the new common guard treated a known incomplete negative infrastructure
observation as unknown effects. Fixed by retaining only the exact expected
typed negative receipt as an observation with completed=false and no repair
authority. Incomplete positive and foreign-family receipts still fail closed.
Native/flat-baseline/integration regression group: 49 PASS. A fresh general
CI is required; the failed run and its JUnit archive remain preserved.

After that gate: module cutovers follow
[the reconstruction plan](POLYGLOT_RECONSTRUCTION_PLAN_2026-11-03.md), and UI
follows [the Studio contract](STUDIO_PRODUCT_CONTRACT_2026-11-03.md).
Rust becomes host authority, TypeScript owns the client/React Studio and
TypeScript language service, and Python remains a reference/lab/compatibility
layer where needed. The external Rust checkout remains untouched.

The original 30 matched pairs, holdout, ablations, unresolved paid UNKNOWN
and historical ordinary-suite gaps keep their existing status. No result is
invented, no paid hold is cleared, and no new provider call is authorized by
the engineering gate. Full reconstruction requires capability inventory and
individual accepted cutovers, not merely copying crates or drawing a UI.
