# P07 matched execution through the model queue — design, 28 October 2026

**Design only. Not an implemented or accepted paid replacement. No provider calls were made for this review. P07 remains OPEN.**

The smallest complete replacement is one Linux matched-arm driver, one capable iterative baseline adapter using the same host safety/check boundary, one strict Windows paid broker adapter, and frozen public/host manifests. Reuse GitHubQueueBackend, full TaskRunner, CheckObligation, the existing Docker executor and the separate candidate oracle. Do not extend the old source-only campaign and relabel its outputs as full P07.

## Findings in the existing paid path

Read: tools/p07_campaign.py; tools/prepare_p07_kaggle.py; tools/p07_calibration_binding.py; tools/evaluation_p07_source_pilot.py; tools/p07_paired_report.py; docs/P07_KAGGLE_CAMPAIGN.md; docs/P07_PROGRESS_2026-10-26.md; docs/P07_BUDGET20_PROPOSAL_2026-10-27.md.

The existing campaign deliberately copies production Python files only, gives each arm one writable source_path, forbids target imports/setup/tests, and runs locally with final syntax checks. Its Atomic adapter restricts role tools through capability_factory, sets max_replans=0, and therefore cannot establish the requested full public agent journey. The baseline iterates read_file/patch_file only and cannot run an ordinary check or perform coupled-file edits. The old binding helpers explicitly label their scope source_scoped_development_not_full_p07.

A concrete disclosure issue is preparation's source_path = next(iter(patch_paths(task["patch"]))). The resulting writable location is derived from the gold patch, even though gold text is removed from the generator packet. A replacement must not reuse that path hint or a scope selected from gold. Redacted private calibration hashes may attest host admission; they cannot justify gold-derived model hints.

The old preparation pins a historical FRAMEWORK constant and a source-only helper digest. It installs the framework independently, then forwards MODEL_PROXY_URL/API_KEY to its generator child. That topology must be replaced by the queue: Linux needs only its automatic GitHub token; physical provider credentials stay in the Windows broker process. Earlier diagnostic/source-only attempt records must remain unchanged and separately identified.

The existing report tool correctly refuses automatic P07 closure and retains a 30-pair denominator, missing/duplicate rows and unknown costs. It expects cost_source="provider" and physical attempt records, while current queue ChatResult accounting and broker journals use their own selected cost/provenance fields. A reviewed explicit evidence adapter is needed; do not rename scripted or estimated usage to provider cost merely to satisfy that schema.

## Minimal proposed components

| Component | Proposed responsibility | Existing machinery to reuse |
| --- | --- | --- |
| Frozen public manifest and host admission manifest | Task/base/source/scope identities, paired order, immutable ordinary runner/image, fixed owner, limits and exact profile hashes | CheckSpec/CheckObligation, raw source snapshots, accepted calibration receipts |
| tools/p07_matched_queue_driver.py, new | Run exactly one declared task/arm into a fresh directory, preflight source/environment, construct queue backend and common arm envelope, collect immutable candidate and receipts | GitHubQueueBackend, TaskRunner, TaskBudgetBackend, completion and graph sync |
| tools/p07_iterative_baseline.py, new | Flat iterative inspect/read/edit/check/finalize loop through the same confined dispatcher, hashes, source lease, budget backend and final gate | Existing tools/ToolContext/ChangeSet/check_obligation/completion components |
| Strict paid Windows operator, new | Exclusive campaign reservation → original call claim → one physical send → selected provider usage settlement → immutable response | tools/p07_model_broker.mjs protocol; durable lock/journal pattern from scripted operator |
| Matched workflow and evidence adapter, new | Sequential arms with exact commit/profile binding, artifacts/download/hash verification, records for paired reporting | Scripted queued journey workflow, existing paired report and separate oracle batch |

These filenames and responsibilities are a proposal, not a claim that the files exist. Keep the first driver small: one task/arm per process; no account chooser, dynamic provider routing, resumable model replay, or asynchronous paid arm fan-out. A coordinator admits the next arm only after the prior arm's physical ledger and artifact status settle.

## Public inputs and deterministic scope

Freeze a strict public-task allowlist: instance_id, public repo, base_commit, problem_statement, dataset revision, and digests of the initial base/source inventory. No patch, test_patch, FAIL_TO_PASS/PASS_TO_PASS, calibration log bodies, changed-path list, gold-derived source_path, candidate answer or grader explanation belongs in a generator packet.

Build the source snapshot from exact base Git objects before model admission. Use a declared byte/file bound and fail on overflow; do not truncate. For an initial Python-only study, writable scope is every ordinary production .py/.pyi file present at that base, selected by a frozen repository-neutral path predicate. Exclude test directories/test filenames, checker configuration, runner anchors, setup/environment/security files, links and generated paths. Enumerate and hash the complete result before either arm runs. No new files or renamed paths are implicitly authorized. If the public problem requires a scope outside that predicate, record it as blocked under the frozen replacement rule; do not consult gold to widen it.

Give both arms identical readable public base source and ordinary tests/configuration. Ordinary tests may be read but their bytes/configuration/runner anchors are immutable. Pin each task's complete host-selected ordinary test file obligation using public base metadata and declared selection rules, independently of hidden test selectors. Hidden test patch contents or requirement names must not be used to choose visible test files. If no suitable public ordinary obligation exists, retain an unavailable case rather than importing the hidden tests as development feedback.

The generator workspace is a fresh temporary public-base project, not the framework checkout containing benchmark fixtures. Model tools remain confined to that project. Target source/setup/tests execute only in the admitted Linux Docker lifecycle, without provider environment or arbitrary network access. The Windows broker never runs repository code. Hidden corpus bytes remain only with the trusted grader/custodian.

## Same authority and limits for both arms

Both arms use exactly the fixed anthropic/claude-sonnet-5@default model, identical temperature/token ceilings, input admission, zero transport retries, physical-call cap, elapsed deadline, cost reservation ceiling, allowed source scope, checker authority and final publication conditions. Arm order is predeclared and counterbalanced within fixed owner cohorts. No quota-based owner switch, task substitution or account fallback occurs.

Every physical call counts: Atomic planning, parser, answerer, critic, reparser, internal replan, repair, and any semantic-index inference. Baseline planning/repair/finalization calls also count. Host-only deterministic indexing is identified separately; any model-backed preprocessing happens after the arm clock/reservation starts and is charged to that arm. A no-cost claim requires a recorded deterministic/no-inference implementation, not omitted bookkeeping.

The public queue smoke's 40-call zero-tariff profile is not the paid experiment profile. The older 30-call/600000 aggregate input/4096 output/900-second/$0.80 source-only profile is also not automatically reusable. Freeze all numeric paid limits and the exact per-request versus aggregate input meanings together, then use one profile hash for both arms. Reserve worst admitted input/output estimates before send; report these as estimates unless an external billing limit has been independently verified.

The proposed $0.22/arm allocation remains a changed experimental profile. Smaller caps can produce early budget stops and must remain in the denominator. They cannot support a claim equivalent to the older $0.80 setting. A pilot is the first declared main pair(s), counted once, not an extra unrecorded tuning campaign.

## Atomic arm

Use the full public TaskRunner flow with its normal tool registry and built-in bounded internal replan. Construct an immutable initial CheckObligation before inference and carry it through tool checks, post-edit/step checks, repair and fresh final completion. Use host planning with public problem text and deterministic public candidates; count that physical planner call. Review/approve the exact predeclared source scope and obligation digest through the normal host mechanism. Do not bypass planning by supplying a hand-authored solution plan.

Provide a normal cancellation/deadline envelope, max model calls and declared repair limits. A completed ordinary assertion failure may authorize bounded repair only through the actual runner/current source/obligation evidence contract. Missing collector completion, skipped required checks, setup/import errors, timeout, unknown effects or charges cannot be converted into repair authority. Final publication requires a fresh successful full ordinary obligation on the actual candidate bytes. Retain graph coverage and post-publication sync outcomes separately; graph-sync failure must not be hidden by task.done.

## Capable iterative baseline

The comparator is a flat iterative agent, not a one-shot patch writer. It can search filenames/text, read complete current files, inspect the same public task input, patch one or multiple allowed files with exact read/hash preconditions, invoke the same host command=check, inspect ordinary failure output, repair and request finalization repeatedly within the same limits. Tool errors remain observations and cannot change scope, deadline or caps.

Use the same host dispatcher/read proof, protected test/config inventory, ChangeSet isolation/publication, unknown-effect latch and CheckObligation derivation as Atomic. A flat loop has no Atomic retrieval/semantic/task-DAG/critic scaffold, but it must not lose the basic debugging capabilities or safety checks available to Atomic. Building a structural index solely for existing host safety validation is acceptable if it is not exposed as extra graph hints to the baseline.

The baseline cannot self-certify completion. Its done action invokes the same independent host final syntax/source/ordinary-test gate and controlled publication. Ordinary failures remain visible and permit further flat-loop repair if the shared envelope still admits it. Unknown or infrastructure outcomes stop/hold with the same policy. Keep the completion claim, actual publication, public-check result and later independent oracle result as separate fields.

## Queue and physical accounting

Each request identity binds campaign, named owner, task, arm, full framework/workflow SHAs and frozen profile SHA256. Backend.name/copied config.name additionally bind the exact queue repo/branch/prefix, preventing approval reuse after a destination or arm/profile swap. Construct tariffs and immutable config before TaskBudgetBackend wraps it; do not mutate its frozen binding later.

The trusted paid operator must validate the complete exact identity/config against its fixed cohort and remaining authority. It holds one durable exclusive lock across campaign/owner/arm reservation, original call claim, physical send and settlement. It must enforce campaign-wide unresolved holds across different call IDs, not only per-call duplicate detection. Record the original reservation owner/day/call and the framework call-to-queue-call mapping. Reserve against all applicable caps before sending; actual cost over reservation stops subsequent admission and stays accounted.

A physical proxy callback performs exactly one provider call, without retry, account/model fallback or automatic resubmission. Fully received malformed model output still settles recoverable provider usage. Unknown physical/charge/submit state retains the original reservation and blocks all subsequent paid admission until explicit reconciliation. Cancellation after enqueue does not prove non-send. Linux polling timeout does not grant another request. Completed response publication may be recovered from its exact durable packet without inference.

The scripted operator is a zero-provider qualification aid and is not this paid adapter. Do not install provider credentials in its script or swap callbacks behind its zero-tariff/profile identity. A paid adapter requires its own frozen profile, admission/settlement tests, physical proxy normalization and operator qualification.

## Ordinary checks versus independent hidden grading

Ordinary in-loop checks run only public base tests under an admitted runner/image. Existing base/gold/wrong environment calibration establishes a separate hidden grading prerequisite; it is not a source of development answers.

After both members of a pair stop and their candidate bytes, publication state and physical ledgers are frozen, send their patches plus exact instance/base/source/runtime/profile identities to a fresh isolated oracle worker. The grader owns hidden test patches and requirement/regression selectors. It mounts neither model credentials nor the generator state, and returns a candidate-hash-bound grade after controls and cleanup succeed. Do not feed this grade back into either paired arm or perform a free follow-up repair.

A model-created candidate can be graded even when publication/final ordinary checks fail, but retain its actual completion state and apply one predeclared candidate-selection rule to both arms. No cherry-picking the best intermediate candidate using hidden feedback. Absent candidate, budget exhaustion, unknown, unavailable and failed cases remain explicit observations.

## Evidence and budget boundary

Retain one immutable attempt namespace per task × arm × declared run: public input/scope manifest; source and runner/image hashes; exact profile/permissions/prompt hashes; original task/envelope/approval identities; all queue call IDs and durable physical reservations/usage; role/tool transcripts; ordinary check receipts and source revisions; task status/replans; candidate patch and byte hash; final gate/publication/graph reports; elapsed time/interventions; and separate hidden grade/archive hashes. Secrets stay process-only and are excluded before publication. Public prompts on the data branch must contain only the admitted public dataset.

The 27 October $20 document is explicitly PROPOSED_UNFROZEN. Its prospective 84 arms × $0.22 = $18.48 comprises 60 main arms, 12 holdout arms and 12 separate ablation arms; owner proposals are $8.80 akseley and $9.68 umirbayulgaisha, within their earlier $9.50/$10 ceilings. Previously booked $0.035611375 and reserved/headroom amounts must be reconciled with current physical ledgers before launch. This review did not query current balances, quota, model availability or tariffs. Do not treat those historical values as freshly verified availability.

Six holdout IDs remain an unfrozen budget assumption in that document. Freeze metadata-only selection, upstream revision/hash, exclusion set for all exposed tasks, repo split, seed/replacement rule and owner assignment before revealing holdout problem statements to development. If repository separation is impossible, label within-repository holdout. Define each graph retrieval/semantic layer/task DAG/critic ablation concretely on registered development cases; no feature-label-only ablation is a complete experiment.

Use the paired-report schema only after a reviewed queue-to-physical evidence adapter preserves complete actual usage, uncertainty and candidate-bound grades. Include all failures/attempts in cost-per-success, retain the 30 main-pair denominator, and keep holdout/ablations separate. Metadata completeness and green CI do not close P07 or establish competitive advantage; independent review and the predeclared uncertainty/utility analysis remain required.

## Concrete remaining blockers

1. Implement and review the full one-arm Linux driver and capable read/edit/check baseline, with identical safety/envelope/check authority and faithful full TaskRunner use.
2. Replace the gold-derived source_path preparation with strict public allowlisted packets and deterministic complete production scopes; register the scope/ordinary-test selection rules before inference.
3. Produce a task-by-task host runner registry for the chosen cohort: immutable image/interpreter/runner identity, public ordinary test inventory, protected anchors/configs, complete source visibility and fresh derive/final checks. Existing hidden calibration receipts alone do not supply this registry.
4. Qualify each actual historical ordinary runner through the new full journey, including known unsupported historical-runtime cases; retain unavailable cases without relabeling calibration as in-loop acceptance.
5. Implement and qualify the paid Windows broker's fixed-owner provider adapter, durable shared campaign/owner/arm accounting, exact call attribution, usage normalization, cancellation/unknown reconciliation and immutable result publication. The scripted operator is insufficient.
6. Freeze the new paid low-cap profile and cohort/order/stop/repair/candidate-selection rules. Reconcile historic spend and current fixed-owner authorization/quota/model/tariffs before any physical inference; no automatic reserve use or owner redistribution.
7. Add the actual matched workflow/one-arm outputs and queue evidence adapter; verify downloaded artifacts and raw bytes against the exact tested source before accepting remote execution.
8. Execute the registered main matched pairs, then independently grade their frozen candidates and report complete costs, missing/failed/unknown cases and uncertainty.
9. Select/calibrate/register the fresh holdout and operational ablations, execute/grade them under their frozen budgets, and complete independent final review.

The root operator is separately qualifying the live scripted queued TaskRunner journey while this document is written. Its result is intentionally not assumed here. Even a successful synthetic public two-file run establishes transport/runner integration for that scope, not paid model generation, a historical task fix, matched quality, hidden grade or P07 completion.
