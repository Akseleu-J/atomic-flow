# Runtime import review, 24 October 2026

Reviewed the runtime/source/reconciliation/capability changes in external source
`c8820ff`, against `8a18fbe`. The external checkout was read only. This review
does not establish production readiness or general live task quality.

## Adopted with corrections

- Source mutation leases coordinate cooperative writers across state directories;
  publication and recovery hold one source lease, checked replacement remains
  reentrant. Durable backup/journal writes improve process-failure recovery.
  External editors remain outside the locking contract. Windows power-loss
  durability and malicious local filesystem actors remain outside acceptance.
- Task capability factories receive the staged workspace on each step and share
  TaskBudgetBackend. Existing language tools reject an LSP client bound to another
  root. Factories and custom tools are trusted host Python; staged binding is their
  host contract. Real analyzer/MCP integration is not established by a Probe tool.
- Reconciliation provides a useful host-only recovery entry point, but imported
  behavior needed correction before acceptance:
  - Settlement appended to today's ledger regardless of original call date.
    It now locates an original reservation under exact task/call identity and
    appends to that original day. Missing, foreign, corrupt or multi-day identity
    is refused. An identical reconciliation does not append another ledger row.
  - Same evidence with a different amount silently retained the old amount but
    returned the new amount. A conflicting amount is now refused.
  - The task became completed before ledger/session settlement. A crash could
    therefore release task accounting prematurely. A durable pending intent now
    leaves status unknown until ledger and session writes have succeeded. Retry
    under the same evidence repairs the gap without incrementing spend twice.
  - Effect reconciliation accepted arbitrary, charge-only or terminal IDs, and
    accepted conflicting evidence. It now requires an actual unresolved effect
    observation, checks session/task project identity, and rejects changed
    evidence for an already reconciled effect. Corrupt sessions are not ignored.

The fixes are in `reconcile.py` and `budget.py`, with independent regressions in
`tests/test_reconcile_import_regressions.py`. Historical imported tests remain.

## Validation

Only offline focused checks are authorized for this pass; no paid API, model calls,
Docker or full suite. First run failed at all 13 pytest fixture setups because
the filesystem sandbox denied pytest temporary-directory cleanup (WinError 5),
before test bodies ran. The raw setup-error log is retained in
`validation/import-20261024/runtime-reconcile-tests.txt`. Final scoped results
in `runtime-final-scoped.txt`: **24 passed**, covering reconciliation, the new
regressions, staged capability composition and source write serialization. An
earlier unsandboxed run had 12 passed / 1 failed because the new regression used
the invalid session outcome `completed`; its fixture was corrected to `success`.
Raw logs are retained. Setup failures are not product failures or passing checks.
An additional final-task-write crash regression passed separately (**1 passed**,
`runtime-final-task-save-gap.txt`); targeted Ruff passed (`runtime-ruff.txt`).
These are focused local checks, not CI or remote runtime acceptance.

The daily reservation gate also now refuses an explicitly unknown price when a
positive daily budget is configured; legacy callers without that optional field
retain their existing contract. A follow-up also closes ordinary cross-midnight
recording: `record()` now resolves exact task/call ownership across retained ledger
days and writes to the existing call's original day. Foreign owners or duplicate
days raise without writing; a late response cannot override a reconciled cost.
Unreserved legacy `record()` calls retain today's append compatibility. Missing
original reservations remain forbidden for host reconciliation. No historical
ledger rows are rewritten or deleted. Cross-day regression evidence is separate
in `runtime-ledger-cross-day.txt`: **43 passed in 726.46 s**, covering new ledger
regressions, reconciliation and existing task-budget tests. Targeted Ruff passed
(`runtime-ledger-ruff.txt`). The slow elapsed time is recorded as observed; this
run was not a performance benchmark.

Ledger reconciliation currently scans retained ledger days to prove original
identity. This is conservative and can become expensive with very long retention;
an indexed transactional store is a future improvement. Calls whose historical
ledger identity is ambiguous require manual inspection rather than guessed repair.
