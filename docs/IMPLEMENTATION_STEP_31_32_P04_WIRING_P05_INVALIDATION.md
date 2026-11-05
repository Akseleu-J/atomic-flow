# Packages 31–32: read-plan wiring (P04-2) and explicit step invalidation (P05-1)

Date: 25 October 2026. Source: clean on top of `9062419` (branch
`experimental`). Model calls = 0.

## Package 31 (P04-2): the step query carries a host read plan

`TaskRunner._read_plan` computes the package-30 context pack for the task's
approved paths and appends a bounded (≤2 000 chars, ≤12 entries) advisory
read order to the step query — each entry with its inclusion reason and the
explicit continuation instruction (`read_file/read_node next_offset`) that
the live pilot's model ignored. Two deliberate design points:

- **Retrieval is untouched**: the plan rides only in the model-visible query;
  `Orchestrator.answer(..., retrieval_query=<pristine step description>)`
  keeps context selection identical. Appending the plan to the shared query
  measurably shifted materialization and broke a real-journey regression
  (`test_task_failed_matching_tests_roll_back_real_edit`) — the split removes
  that coupling.
- **Degrades explicitly**: any graph failure turns into a visible
  "Host read plan unavailable (...)" notice, never a silent skip.

Evidence: `tests/test_task_read_plan.py` (2 passed: plan in query with
reasons + next_offset instruction; explicit degradation) plus the full task
suites 46 passed including the previously broken journey test.

## Package 32 (P05-1): invalidating done steps is explicit and cascading

- `Step.invalidated_reason` (default `""`; old task JSON loads unchanged).
- `TaskRunner.invalidate_step(task_id, index, reason)` under the task lease:
  requires a non-empty recorded reason; only `done` steps; cascades to done
  steps whose transitive `depends_on` includes the target; rewinds
  `current_index`; clears per-step result/request state; reopens a
  failed/paused task as `paused`. Completed tasks and published/publishing/
  recovery-conflict changesets are refused outright — external effects are
  never replayed; a new task is required.
- CLI: `atomic task --invalidate-step INDEX --task-id ID --reason "..."`
  (requires `--project`).

Evidence: `tests/test_step_invalidation.py` — 4 passed: cascade with recorded
reason, independent done steps spared, all four refusal classes (empty
reason / unknown index / non-done step / immutable completed task), and the
requeued state surviving save/load. Neighbour suites (`test_cli`,
`test_task`) green; ruff/compile clean.

## Not claimed

Full P05 task-DAG states (superseded semantics beyond existing replan),
frontier planning API and evidence links; P06 autonomy envelope. The
invalidation path does not yet verify that the invalidated premise is
actually false (e.g., digest of the source it edited) — the host asserts it
and the reason is auditable; automatic premise checking is P05 continuation.
