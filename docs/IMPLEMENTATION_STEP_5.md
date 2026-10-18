# Package 5: durable task budgets and coordinated recovery

Continues the execution safety/Docker CI branch. All validation model calls are scripted/mocked.

## Behavior

`atomic task` persists its task before planning. Planning, JSON repair, replanning,
role/tool-loop calls and responses followed by parsing failure or cancellation share
one task account. `--dry-run` saves a paused plan and its planning spend; it still calls
the planner. Programmatic `run_plan` does not account for planning performed elsewhere.

Each call saves an in-flight event before submission, with its ID, phase, backend/model,
prices, output limit and reservation. Valid response cost is saved immediately. Daily
ledger settlement deduplicates by call ID; Orchestrator does not add the cost twice.

Before submission the host checks `task.max_model_calls` (default 100), remaining task
USD budget, and estimated daily spend under a shared OS lease. Concurrent cooperating
task processes cannot reserve the same daily remainder. Holds persist after a crash.
Not-sent calls release their hold. Unknown/in-flight charges pause and block automatic replay.

Missing/nonfinite/negative response cost is unknown, not zero. Unknown pricing blocks
submission when a USD limit is active. For a confirmed free backend, configure both
input/output prices as zero in trusted/user config. Kaggle routing preserves these prices.
Missing usage metadata in HTTP adapters no longer fabricates a zero estimate.

Task-owned backend copies set a finite max output (default 4096 if absent) and disable
transport retries without mutating the original backend. Schema repair uses separately
accounted calls. Existing optional-parameter negotiation after HTTP 400/422 remains;
the call cap counts `chat` invocations, not every HTTP exchange.

A budget pause restores the current step checkpoint and marks the step pending before
publication. Known-cost work can explicitly resume after raising its appropriate limit.
Unknown charges require usage review even after a runner restart. An interrupted plan
has `plan_ready=false` and cannot silently complete with zero steps.

Offline `--recover` now takes the runner's OS task lease before reading/changing state,
so it cannot race a running task's publication or overwrite its task JSON.

## Limits

- Monetary reservations use declared prices, UTF-8 bytes plus a framing allowance, and
  max output tokens. They are admission estimates, **not an absolute provider billing cap**.
  Reported excess is saved and pauses more calls/publication; account-level guarantees
  require provider-side spending limits.
- Shared reservation exclusion applies to cooperating task calls. Ordinary ask/API traffic
  retains its previous check-and-record ledger without advance reservations.
- A dedicated CLI for reconciling unknown holds against provider receipts is still open.
  Do not clear a hold merely to bypass an uncertain charge.
- UTC daily files can retain a conservative previous-day hold for a call crossing midnight.
  Cross-day reconciliation and filesystem power-loss transactions remain open.
- Free USD pricing does not mean unlimited provider quota; call counts still apply.
- Synchronous in-flight provider calls still finish or time out before local cancellation.

## Validation

Windows: **193 passed, 9 skipped** (including 23 new budget acceptance cases),
`validation/step5-final.txt`. Ruff on affected code/tests and compileall pass.
Cases cover planner repair, replanning, dry-run, no-call budget refusal, unknown charge,
interruption, cancellation, competing-process reservations, rollback/resume, corrupt
accounting and recovery lease exclusion. Remote CI results are recorded separately.
No paid inference or Kaggle session was used.
