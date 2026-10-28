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
  -> publication: accepted on 4285540d087b87be49e23ccd7fb03ba0a87e4203, CI40633693098.
  Artifact12792532996 ZIP SHA256 caef323495192db6a302b4d704140f359f7e50a2787e19da2313383e496d921b;
  source commit, task failure/current revision, one replan, exact final bytes and
  publication gate were audited from the downloaded artifact. Provider calls=0.
  Main checks40633692030/40633697185 and interoperability40633692152/40633697282 succeeded.
- Independent read-only review completed for command routing, policy, guard and
  journey scope. It found no blockers in the original declared scope and noted
  asymmetric alias restrictions, now corrected prospectively. The follow-up independent
  review confirmed symmetric restrictions; its offline malformed-argument
  robustness note was fixed with list/dict regression tests. This is not the final independent P07 review.

All 30 isolated historical environment calibrations remain accepted. Full
historical in-loop runner admission, matched paid pairs, fresh holdout, ablations
and final independent review remain open. The allocation proposal in
P07_BUDGET20_PROPOSAL_2026-10-27.md is unfrozen and has not changed active budgets.

## Host-selected check command

The next package adds command=check, requiring an existing host CheckObligation.
No target narrowing, model-selected runner/image/environment or host fallback is
allowed. Native obligations reject the misleading command=pytest spelling;
installed-pytest obligations preserve it for compatibility. Policy evaluates restrictions for both equivalent spellings so neither check
nor pytest can bypass explicit deny or ask rules. Because policy has no host
obligation context, this conservatively also applies check restrictions to
legacy unbound pytest calls.
The actual runner remains in the host observation. TaskRunner applies the same
completed-failure guard to the alias. Failure evidence uses one current workspace
snapshot for both the guard and recorded revision.

Focused command/policy/envelope/obligation neighbors: 86 passed; Ruff passed.
The public Docker journey matrix exercises both spellings. CI40634036115
on 0de098aaeff5a6f6294353ef78730818b68aafe0 accepted both failure/repair/publication
journeys. ZIP12792462483 check SHA256198729db914a65c3dd89ee19345f13e4b161f834f083cf7c5b79137cc1cd857f;
ZIP12792169663 pytest SHA25691f652e907a9b074c49175470174e6d2dc182b24d580a7cbb7a6e2489a082cba.
Both archives, tested SHA, initial deterministic failure, one replan, exact final
source and publication gate were audited. This matrix uses installed pytest,
not native SymPy. Symmetric alias-policy hardening was added after independent
read-only review identified the original one-directional compatibility; fresh CI
for that follow-up remains pending. Focused symmetric policy and command suite:
54 passed, Ruff passed. Static inventory refreshed: 472 Python files, no missing
absolute internal imports. Internal plan verification labels still use legacy pytest; generic
ordinary_test labels and historical runtime profiles are separate unfinished work.
