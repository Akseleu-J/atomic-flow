# Capable flat baseline: proposed implementation boundary — 28 October 2026

**Read-only design. No implementation, tests, subprocesses, Git operations, provider or API calls were performed for this document. The proposed core interface requires review before coding.**

A baseline can share TaskRunner's safety machinery without sharing its model planner, parser/retrieval/critic pipeline or model-facing task DAG. The smallest safe implementation adds a fixed host-owned flat execution strategy and a deterministic continuation strategy. It must close a failed attempt before any further mutation and retain its complete negative transcript. Reusing the current Orchestrator or hiding earlier failed records cannot establish this comparator.

## Actual core behavior that constrains the implementation

- TaskRunner.run_plan creates/reviews/approves a Task and enters resume. resume acquires task_lease, checks durable model accounting/effects, constructs one TaskBudgetBackend, validates approval/runtime and enters _resume_locked.
- _resume_locked owns the ChangeSet and normal final completion/publication path. It calls assess_completion, require_scope, publish and _sync_graph. Preserve that path for both arms.
- _execute_step currently unconditionally computes _read_plan, injects frontier context and invokes Orchestrator.answer(mode="medium"). That exposes Atomic retrieval/parser/critic behavior. A baseline needs a separate execution branch before those calls.
- _execute_step also tracks actual applied hashes in the ChangeSet and calls _verify_step. Those host operations must remain shared.
- _verify_step scans every record. A failed run_command followed by a later mutation raises verification_contract_failure with “failing command predates a later mutation”. It is correct to reject that mixed attempt. Do not discard, relabel or reorder the failed record.
- _resume_steps checkpoints the whole attempt and restores it after failure. _replan_failed_step requires the current rollback revision to match current completed-failure evidence. A completed failure observed on edited candidate bytes may become stale after rollback. A continuation cannot reuse that evidence as if it checked restored bytes.
- file_ops transactions themselves roll back when post-edit ordinary verification fails. A tool's failed candidate receipt can consequently refer to bytes that are already absent when _check_failure runs.
- _replan_failed_step currently calls the model planner, then checks envelope preservation, lineage, source/runtime binding, scope, caps, no-progress and approval renewal. Its admission and replacement-application logic are useful independently of its model call.
- step.failure has an exact persisted schema of kind/evidence/rollback_revision; v2 evidence binds its step index and started_at. New revalidation metadata cannot be inserted into that schema ad hoc.

These constraints also mean the baseline cannot be implemented merely by returning a fake planner JSON object from an LLMBackend or subclassing TaskRunner to filter result.tool_calls.

## Proposed host API

Add a fixed immutable host contract, for example FlatBaselineProfile in a new agents/flat_baseline.py:

    FlatBaselineProfile(
        protocol_version=1,
        allowed_tools=("find_files", "search_text", "read_file", "read_result",
                       "patch_file", "patch_files", "run_command"),
        check_command="check",
        continuation="completed_ordinary_failure_v1",
        prompt_sha256=<fixed flat prompt digest>,
    )

No callable, arbitrary runner/command, path authority, credential, native tools, hidden task fields or dynamic tool factory is accepted by this contract. Validate exact built-in names, immutable sorted tuples and fixed protocol values. Compute a canonical profile digest. Root configuration still supplies the same explicit task max_model_calls, max_budget_usd, max_replans/repair ceiling, wall deadline, fixed queue backend, allowed source scope and CheckObligation as the matched Atomic arm.

Proposed TaskRunner extension:

    TaskRunner(..., *, check_obligation=None, flat_baseline_profile=None)
    runner.run_flat(query, *, allowed_paths) -> Task

The default None branch retains existing Atomic behavior, approval serialization and runtime digests. run_flat creates one generic host execution objective, not a solution plan: resolve the public problem within the reviewed existing production scope and complete the full ordinary obligation. The flat prompt receives the original public task, deterministic source inventory and tool schemas. It receives no handpicked gold path, solution substeps, graph read plan, ranked context, parser inference, critic verdict or dependency explanation.

Bind the non-None profile digest explicitly in review/runtime/envelope identity and require exact equality on resume/continuation. No capability_factory is needed; retain its current internal-replan refusal. The fixed strategy constructs its actual dispatcher allowlist directly from the approved flat profile and the enclosing host tool envelope. A custom Python callback cannot silently choose capabilities.

Use the existing task's single TaskBudgetBackend and call_scope for every flat model turn, with a genuine baseline turn/repair phase label. Host-generated phase boundaries and deterministic replacement records create no model_request or physical cost event. Never manufacture a zero-cost planner response or report a host transition as model generation.

## Flat turn executor

Implement a small FlatTurnExecutor that is called only by TaskRunner._execute_step's explicit profile branch. It returns a host result with the same real tool_calls, ledger identity and verdict fields needed by tracking/_verify_step; verdict is "unverified", never a fabricated critic "ok".

The accepted model protocol is one strict text JSON action:

- {"tool":"<allowed built-in name>","args":{...}}
- {"done":true,"summary":"..."} for a completion request, not host success.

One genuine backend call produces one action. Use llm.history.RequestHistory admission/compaction/repeated-call guards and the existing bounded artifact/result-store contracts. Preserve the original query and host failure observations as data. No retry on unknown model outcome, no account/model substitution and no model-backed summary outside the same task budget.

Build ToolContext with the actual private ChangeSet workspace, current admitted file hashes, normal policy/trust/approval callbacks, UndoStack, LargeResultStore, enforce_read_before_edit=True, exact mutation_scope, reviewed Docker execution config, the original CheckObligation, cancel/action boundaries and the shared EffectState/journal sink. Host structural indexing remains permissible for confined-path/hash/read validation; no graph ranking, node recommendations or context pack is emitted to the baseline.

Call agents.loop.execute_tool for every action. It already validates role/tool/schema/scope/policy, records every outcome, gates lazy invocation, invalidates failed read proof, checks output contracts and latches uncertain effects. Do not call tool.run directly. Restrict model run_command arguments to command="check" with no target/selection; that command derives and runs the complete host ordinary obligation.

After every tool, persist the full record/result before checking effects, cancellation or requesting another model response. Successful applied edits are tracked by their actual after_hash values through the same TaskRunner code used by Atomic. Complete read coverage is current-byte proof, not a filename list or transcript memory.

Reads and successful mutations may continue in one flat attempt. The executor must return immediately on the first error/denied/rolled_back/unknown check outcome, before another model request or mutation. It must also stop on a completed negative check. The failed record remains the last action in that attempt. _verify_step therefore sees it honestly, including its ordinary receipt. A completion request ends the attempt and invokes the same automatic fresh step/final verification as Atomic.

This permits real search/read/multifile transactional edits/checks and repeated repair across explicit authorized attempts. It does not permit an unsuccessful check and later edit to be merged into a successful attempt.

## Deterministic continuation instead of model replanning

Refactor _replan_failed_step into three parts with existing semantics:

1. Validate current failure, rollback/source/runtime/obligation/accounting/effects/scope/deadline; enforce the shared repair/no-progress/counter limits and begin_repair once.
2. Select replacement work: existing replan_task for Atomic; a fixed generic flat continuation for the baseline.
3. Apply the replacement through the existing lineage/envelope/source comparison, review renewal, task history, capped counter and persistence code.

Only part 2 differs. The baseline's replacement preserves the same complete scope, verify_with="ordinary_test", original public objective and genuine prior observations. It appends one host-owned sequential attempt without making a planner call. The model is asked to continue debugging in its flat conversation; the host supplies no inferred fix or location.

Use the same max_replans counter as the bounded continuation limit and explicitly record transition_kind="flat_host_retry" in an append-only host journal event. The report distinguishes host retry count from planner physical calls (zero). Do not claim an absence of host safety scheduling: internal serial records are bookkeeping, while the baseline receives no task-DAG scaffold.

The existing classify + begin_repair/check_repair_budget semantics remain shared: the first authorized episode starts its cap once, and further retries or resume cannot reset that allowance. The existing 25%-of-remaining repair rule must be identical for both arms under the frozen experiment profile. All flat repair inference is tagged to that durable repair identity.

## Required rollback revalidation phase

A new narrow host phase is necessary where the original completed failure's checked revision differs from the verified restored workspace. Without it, repeated flat debugging after a rejected candidate cannot be admitted safely.

The proposed API is a fixed helper, not an arbitrary continuation callback:

    revalidate_flat_failure(task, failed_step, original_host_observation,
                            restored_manifest, check_obligation)
        -> HostRevalidationPhase | Refused

It runs only after the original failure transcript, candidate revision, actual completed-negative receipt and rollback result are durably retained. It requires all of the following:

- The trigger was a real complete ordinary assertion failure from the admitted runner, not model text, missing output, timeout, denied tool, setup/import/collection error, source conflict, unknown charge/effect or failed rollback.
- The dispatcher/tool transaction proves which candidate bytes were checked and that restoration finished without uncertainty. Tool summaries alone must not substitute for validating the retained host receipt.
- The same immutable obligation can derive on the actual restored bytes; source scope and all runtime/approval/accounting/effect/deadline gates still hold.
- A new full ordinary check now returns an independently completed assertion failure on those restored bytes.

Persist this as a **new host revalidation phase**, with its own index/started_at and receipt binding. Retain the earlier failed phase and its complete records unchanged. The new phase provides current v2 failure evidence to the normal bounded continuation gate. Do not overwrite the original candidate failure's proof, substitute the old digest for the restored revision, or erase a rolled-back tool result from the transcript.

Implementing that phase requires a strict host-owned phase kind in persisted task state. The smallest reviewed change is an optional versioned flat-phase ledger mapping its sequential phase indexes to ordinary Step indexes, transition kind, predecessor receipt hash, source-before/after and host receipt hash. Validate this ledger strictly in task loading and bind its schema/profile version in the approval/runtime digest. Keep legacy tasks without the field unchanged. Phase kind must not be inferred from a description string or accepted from model JSON.

A host revalidation step contains no model call and no mutation, so its own attempt checkpoint/rollback revision is unchanged. Its current completed-negative evidence can pass the existing _check_failure/failed_observation_matches/_replan_failed_step contract honestly. Any internal failed predecessor becomes superseded only through the normal accepted replacement transition; its failure/proof history remains retained.

If the fresh restored check passes, skips, errors, times out or is incomplete, it does not supply current failing evidence. Stop/pause under the applicable existing policy; never invent a failure from the public issue or use a hidden test. In particular, a failing edited candidate followed by passing restored public tests is not automatic authority for another repair under this policy. Record that practical limit equally for both arms. A broader policy would be a separately reviewed change, not a baseline exception.

## Final gate and artifacts

Keep _resume_locked's ordinary assess_completion → require_scope → ChangeSet.publish → _sync_graph sequence. A model done request or locally passed command cannot bypass a fresh complete source-bound final obligation. Preserve task completion, actual candidate bytes, publication, post-publication graph status and later hidden oracle grade as separate fields. The separate hidden grader runs only after candidate selection/freeze and returns no repair feedback.

Artifacts must contain all phases, negative records, rollback/revalidation receipts and original source revisions; every real model request/response and actual usage; shared task/repair/campaign reservation identities; approvals/runtime/profile hashes; complete public scope; final gate/publication/graph results; no-candidate/paused/unknown reasons; and host retry versus physical planner counts. Holdout/hidden data never enters baseline context.

## Concrete implementation changes still required

1. Add/validate/hash the immutable FlatBaselineProfile and a strict optional flat-phase state schema; retain absent/default legacy serialization.
2. Add TaskRunner's explicit flat execution branch and run_flat entry point, bypassing _read_plan/frontier prompt/Orchestrator only for that host profile.
3. Extract the existing shared applied-hash tracking/result verification tail so both executors use it unchanged.
4. Implement the flat JSON turn loop on execute_tool, common ToolContext/policy/read/effect/result/history contracts and the same TaskBudgetBackend.
5. Split planner-independent repair admission/application from replan_task; add the deterministic generic replacement strategy with identical envelope/lineage/repair caps.
6. Implement the narrow durable rollback revalidation phase and raw receipt linkage. Unknown/infrastructure outcomes must remain ineligible; preserve every failed record.
7. Add focused regression tests for negative-check attempt closure, later mutation refusal, rollback revision mismatch, current revalidation, rejected-candidate receipts, no repair-cap reset and no synthetic planner/model events.
8. Run an actual public coupled-file Docker/queue baseline smoke with at least one failed attempt, current revalidation when needed, genuine model/scripted flat actions, final gate/publication and explicit zero-provider scope for scripted qualification.
9. Only after both actual arm paths qualify, freeze the matched paid profile/permissions/caps and run independently graded historical pairs.

This design intentionally leaves _verify_step's stale-negative guard intact. The core approval decision is whether to add the fixed flat execution/continuation profile and explicit host revalidation phase described above; there is no safe claim that the current source-only baseline already implements them.
