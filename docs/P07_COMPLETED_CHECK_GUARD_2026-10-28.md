# P07: completed check failures before bounded repair

28 October 2026. P07 remains OPEN. No provider calls were made in this package.

A CheckObligation-bound exit code 1 previously entered the deterministic repair
path even when no valid runner receipt existed. Bootstrap, import, collection or
incomplete execution must not authorize automatic repair.

The host now revalidates the stored raw stream, nonce, immutable CheckSpec,
unchanged before/after source and runner receipt. Installed pytest receipts record
call/setup/teardown failure counts. Only an assertion-stage failure with no fixture
failure or skip qualifies for bounded repair. Native SymPy requires counted test
failures with no exception, required skip or unexpected pass. A successful check
still requires exit zero and the existing complete positive receipt.

Tool summaries retain bounded contract/receipt digests, runner, timeout/resources,
completion flags and checked revision. They omit the full specification and raw
source/output. TaskRunner checks this trusted host observation against its current
immutable obligation and current workspace revision. Model text is not evidence.
A post-edit failed check whose candidate bytes were rolled back is stale at the
restored revision and pauses; it does not acquire repair authority from that old
failure. Legacy checks without a host obligation retain their previous behavior.

Receipts are integrity evidence within the trusted host, not signatures,
independent oracle grades or protection against a target that falsifies its own
runner output. The separate hidden oracle remains required for benchmark quality.

## Validation

- Focused initial neighbor suite: 190 passed (check execution, native SymPy,
  obligation contracts/integration, internal replan and failure policy).
- Additional native negative cases and edit/result neighbors: 98 passed.
- Ruff: passed. Real Docker public failure -> replan -> coupled repair -> fresh check
  -> publication: pending on the package source commit.
- Independent review: requested; the previous reviewer stopped on an account
  usage limit. Root review is not an independent review.

All 30 isolated historical environment calibrations remain accepted. Full
historical in-loop runner admission, matched paid pairs, fresh holdout, ablations
and final independent review remain open. The allocation proposal in
P07_BUDGET20_PROPOSAL_2026-10-27.md is unfrozen and has not changed active budgets.
