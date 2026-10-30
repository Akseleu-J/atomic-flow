# P00–P06 acceptance audit — 26 October 2026

Scope: the original numbered P00–P06 criteria in `PRODUCTION_ROADMAP.md`,
closure reports, named test definitions, and the new offline CI verifier.
This audit does not independently review all runtime code. No tests, model
inference, external project code, commit or push were executed by this reviewer.

## Criteria registry

`validation/p00-p06-final-20261026/acceptance-matrix.json` maps all **44 original
numbered criteria** to exact pytest module/function identities. Every referenced
definition was checked for existence in the current source. Parametrized cases
must all pass on both host Python versions. Mapping a criterion is not a passing
grade; the registry remains pending until the final exact-source CI seal.

The P00 E00/E01 additions are retained through strict identity/provenance,
zero-denominator and source-span cases in P01/P04. Walkthrough artifacts are
source-bound, scripted, selected-source exercises. They do not establish full
repository semantic completeness or model utility. Four production blocks and
P07 independent quality acceptance retain their original scope and status.

## Findings and corrections

| Finding | Consequence | Resolution |
|---|---|---|
| A verifier accepted any two successful workflow projections and did not bind jobs/artifacts to their runs | Unrelated green execution could be mistaken for this source | Require the two exact workflow names, distinct run/workflow IDs, job run IDs, artifact workflow run/head binding, and tested-merge artifact names |
| Required job lists and host identities were not checked | A truncated job projection or duplicate Python artifact could hide missing checks | Require all six checks jobs and thirteen interoperability jobs; distinct artifact IDs/names; actual Python/platform evidence and JUnit counts |
| An empty criterion matrix or empty case list passed vacuously | A missing chapter could be reported verified | Require exactly all 44 original numbered criterion IDs with nonempty exact case references |
| Empty composition scenarios passed `all([])` | No scenario execution could receive a positive result | Require nonempty scenarios and every scenario passed |
| JUnit identities and archive members could be empty/duplicated | Ambiguous evidence could be counted or overwritten | Reject empty/duplicate testcase identities and duplicate/oversized archive members |
| P01.3/P02.1 require actual move identity, not only identical-content inference | An edited owned move could lose its old→new ID mapping | Root implemented verified move-observation forwarding; its two owned-move cases passed and are included in the registry |

The verifier reads archives as data without extraction or execution. It checks
ZIP digests, JUnit digests, committed source hashes, head/merge tree equality,
both mandatory workflows/jobs, every criterion case, and actual cross-platform
PASS coverage for every skipped host case. A wheel installation does not cover a
skipped runtime test.

The verifier implementation and its new tests were written by this reviewer
after the findings were reported. Parent peer review remains necessary; this
document does not label the reviewer's own implementation independently accepted.
Root's serial verifier run completed **36 PASS**, 1.64 seconds, no skips:
`validation/p00-p06-final-20261026/verifier.xml`. This is local evidence, not CI.
Tests include complete synthetic seals, metadata/source/runtime/JUnit tampering,
empty matrices/scenarios, duplicate evidence, and an uncovered platform skip.

## Decision

**Acceptance pending.** The registry and locally tested stricter verifier are
ready for the final source-bound CI audit. Old green evidence on
`995e3bb` remains direct P06 evidence, not acceptance of the current uncommitted
P00–P05 integration. Working-file hashes for this audit are recorded in
`validation/p00-p06-final-20261026/acceptance-audit-source-hashes.json`.

Offline verification checks consistency of retained API metadata and downloaded
artifacts; it does not turn editable local JSON into independently signed
provenance. Release provenance remains the separate production release gate.
