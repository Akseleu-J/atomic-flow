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
