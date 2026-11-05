# Package 35: bounded internal replans and independent night-session review fixes

25 October 2026. Starting HEAD: `20cdd7767ad846ad42dd9d08fcd709c4d5c11b0d`,
branch `experimental`. This package is a local uncommitted runtime change until
an exact-source CI/consumer decision says otherwise. Live provider calls = 0.

## What the night report actually establishes

The nine named commits exist in the active checkout. P04-2 carries an advisory
read plan into the model query while retaining the clean `retrieval_query`;
P05-1 adds explicit step invalidation; P06-1 is a pure predicate, not autonomous
runtime execution. GitHub confirmed both workflows for the starting HEAD:

- [Project/container checks](https://github.com/Akseleu-J/atomic-flow/actions/runs/40159998948): success.
- [External interoperability/installed release](https://github.com/Akseleu-J/atomic-flow/actions/runs/40159998952): success.

Those statuses do not cover this working diff. We did not independently audit
every earlier commit's CI, recheck any Kaggle quota or run live inference.
Green CI did not expose the independent review defects below.

## P06-2: explicit opt-in, compatible default

`TaskRunner.review/approve/run_plan` accept `allow_internal_replan=False`.
The default still produces schema-1 approval and replanned work still pauses.
Passing `--yes` does not enable autonomous replans.

An opt-in review produces schema-2 approval with a strict `internal_replan`
envelope included in the digest. Approval must repeat the same option and
exact path scope. The envelope fixes:

- Built-in source/check tool allowlist, enforced in the real dispatcher through
  role tool sets, rather than relying only on advisory `tools_needed`.
- A verification floor at least as strong as the strongest original active
  step, plus retention of declared edit/execution obligations.
- Positive finite enforced task budget, physical call cap and replan cap.
- A binding to declared config, actual backend name/effective model/endpoint
  config, execution settings, project trust and policy-file hashes.
- Bounded first-line failure history for the no-progress guard.

Raw API keys are excluded from persisted runtime bindings. The host remains
responsible for credential ownership/grants; the digest is not a credential
attestation. Routing and injected capability factories are unsupported in this
opt-in slice and require normal explicit plan review instead.

### Execution and refusal

After a failed step has a verified workspace rollback and independent runnable
work has drained, the runner checks original approval/scope, effects/accounting
holds and runtime binding. A valid replacement receives a new plan digest
inside the old path/tool/budget envelope and continues in the same loop.
There is no nested task lease or recursive resume.

Legacy approval, new tools, weaker checks, exhausted budget, configuration or
provider drift, unresolved effects and repeated failure signatures do not
authorize automatic continuation. Source/scope drift during renewal refuses
the new approval. Replans keep the original task query and existing completed
work; the model cannot provide a new path grant.

Repeated `StepFailure` signatures use the error message, excluding the changing
step index. Empty/whitespace failures no longer raise IndexError. Oscillation
tracking is exact-signature detection, not semantic detection of every repeated
strategy; independent maximum replans/model calls remain mandatory.

### CLI and SDK

```powershell
atomic task --review TASK_ID --scope-path app.py --allow-internal-replan --backend BACKEND
atomic task --approve TASK_ID --plan-digest DIGEST --scope-path app.py --allow-internal-replan --backend BACKEND
atomic task --resume TASK_ID --backend BACKEND
```

Use the actual task ID, configured backend and digest from the matching review.
Review/approve construct the selected backend locally to bind its effective
identity; they do not send model requests. Model overrides must match on resume.
The SDK accepts the same explicit keyword. Switching from an unconfigured
backend to a configured one requires a new review.

## Review fix: graph CAS and unresolved additions

An independent agent found that `GraphStore.commit` compared only an in-memory
generation. Two independently opened handles could both commit generation N
and overwrite one another. Atomic rename alone was not persisted CAS.

The commit now uses a shared persistent OS lease and compares the on-disk
generation while holding it. A stale handle or concurrent writer fails before
replacing the committed snapshot. Directory sync follows the existing platform
durability contract; no new power-loss guarantee is made.

`apply_delta` captures its base generation before relinking, and callers may
create a delta with `base_generation=...` to bind deferred work to its planning
snapshot. For compatibility, a legacy delta without that field binds at apply
entry; it does not retroactively prove which generation the caller planned on.
An absent old digest means expected file absence, not unconditional replacement.

Relinking now considers stored raw imports as well as old resolved edges. Adding
a previously missing imported module, or changing module/package precedence,
therefore refreshes previous unresolved consumers without reparsing unchanged
files. Tests compare delta results to clean builds and include two simultaneous
writer processes. Re-export resolution/fixed-point semantic completeness is
still open in the underlying name resolver.

## Review fix: project-bound invalidation

`invalidate_step` now checks `task.project` against the runner's project before
changing persisted state, even when no changeset has been created. A mismatched
root leaves the task file unchanged. `invalidated_reason` is validated as text
on load/save. CLI rejects combining invalidation/reconciliation with another
task operation, including step index zero.

## Validation and remaining boundaries

New regressions cover opt-in/digest persistence, same-resume repair, legacy pause,
verification downgrade, tool expansion, actual backend drift, repeated failures,
finite limits, cross-project invalidation and persisted CAS/delta parity.
A real Orchestrator/dispatcher test uses a scripted backend and verifies that an
undeclared `run_command` never reaches its implementation.

Final local results and source hashes are recorded in
`validation/p06-continuation-20261025/`. Historical night-session results are not
summed with new runs or presented as CI for this diff. No benchmark quality,
provider billing, remote worker acceptance or production readiness is established.

Final local suites, disjoint by test file: **79 passed** focused P06/review/graph
cases (38.39 s), **85 passed / 2 skipped** neighboring task/budget/CLI/execution/
composition cases (153.81 s), and **4 passed** new CLI opt-in cases (1.06 s).
Total for these final selections: **168 passed / 2 skipped**; earlier overlapping
42/4/30-case development runs are not added. Ruff, compileall and diff checks PASS.
An initial neighboring-suite command named a nonexistent `test_state.py`, so it
ran no tests; the corrected selection produced the 85/2 result above. Windows
desktop sandbox process/temp restrictions required approved local execution
outside that sandbox. This is not a full-suite or remote execution result.

P04 still computes its advisory pack via `project_files` and a clean graph build
for each step; goals are all approved paths, and only the first bounded entries
are shown. TaskRunner's primary source index still uses the legacy refresh path.
The delta store has not replaced the entire live context/retrieval hot path.

This is a conservative P06 slice. Its global verification/edit/check obligations
can reject a replacement even when some original work is already done; the runner
pauses rather than inventing another edit to satisfy the guard. It does not prove
semantic equivalence of a changed plan, grant arbitrary commands/network access,
support untrusted plugins or replace independent requirement evaluation.

Next bounded packages: step-focused read plans with reusable revision-bound graph
snapshots; graph inspect/explain CLI; task frontier/evidence links; complete
resolver invalidation/re-export cases. Full P02/P04/P05/P06 and the four production
acceptance blocks remain OPEN.
