# P07: typed completed ordinary-check failure evidence

28 October 2026. Prepared implementation; fresh exact-source Docker CI pending.
P07 remains OPEN. No provider calls or paid inference were made.

A bound ordinary-test failure now records version2 evidence with check=ordinary_test,
actual runner (pytest or sympy-native), completed-negative flags, source and
checked-source revisions, attempt identity and obligation/spec/receipt SHA256
bindings. TaskRunner emits this only after the existing host receipt and current
workspace guard succeeds. This removes the misleading pytest failure label for
native checks without rewriting old task approvals or plans.

Version2 has an exact field set, strict integer/boolean/digest validation and
requires equal source/checked revisions. Typed test failures accept only the
matching test category. Unknown fields, bootstrap or incomplete evidence,
stale-source proof and kind mismatches do not authorize repair. These are
integrity checks within trusted host state, not signatures or external attestation.

Legacy version1 keeps its exact original field set and check whitelist. Unbound
pytest and syntax/ruff/mypy failures continue to produce version1 evidence.
Existing durable task loading uses the same versioned validator; a new roundtrip
case checks preservation and classification after save/load.

Root validation: 118 related tests passed (failure policies, completed receipt
guard, internal replan, obligation integration); 51 version2 cases passed after
the durable roundtrip addition. Ruff passed on the initial package. Independent
read-only review and exact-source native/public Docker journeys are pending.
Generic ordinary_test planner/config/edit/completion labels remain a separate
migration described in P07_ORDINARY_TEST_LABEL_MIGRATION_2026-10-28.md.

Independent review identified a resume replay gap: a previously saved negative
could be reconsidered after approval of a different obligation. The replan
consumer now requires the current obligation digest, actual runner and source
revision to match version2 evidence before any model call. Missing/changed
contract or source pauses for a fresh host failure check. Four regressions cover
missing obligation, replaced digest, changed runner and changed source. Updated
focused suite:114 passed; Ruff passed. Follow-up review requested.

Follow-up independent read-only review confirmed the replay guard. Real public
Docker matrix CI40638505847 on2903ccd42855f23ecaf5dcb224a16863a710e457 accepted
both command spellings with strict version2 ordinary_test evidence, actual pytest
runner, one replan and publication. Downloaded archives audited:
pytest artifact12794442507 SHA2563900f223cbd2634f16730fadf0f8a5ff4407d0cc8968713ea701cbb52e98d7bd;
check artifact12794248569 SHA256ae7a7d76f73e35b541f73ceddadb2bf8a14eac535bea06f9e2ef4b642d120694.
Current strict calibration-admission inventory:40 tests passed, all30 accepted
rows preserved. Exact-source native journey and remaining main CI are pending.

ACCEPTED on2903ccd42855f23ecaf5dcb224a16863a710e457: native full-source journey
CI40638502706 SUCCESS; archive12794188079 SHA256
f9f957b12d3bca1610b4750ee0690167e15f4447306ab36e1ef2a8ca81f2b89a
downloaded/audited. Actual v2 ordinary_test/sympy-native failure, one replan,
completed native PASS/final gate and only probe publication across1805 files.
Graph sync remains explicitly failed after publication and unaccepted. No hidden
oracle/model-quality claim. Main checks40638502015/40638508885 and installed
interoperability40638502011/40638509027 succeeded. Both public alias journeys
and native execution/publication are accepted; full P07 remains OPEN.
