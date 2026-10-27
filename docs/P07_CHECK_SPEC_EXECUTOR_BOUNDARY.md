# P07 CheckSpec executor boundary and remaining runtime integration

Source review, 26 October 2026. This document describes the standalone boundary
implemented in `check_execution.py` and the optional host-spec path in
`agents/execution.py`. It does not claim integration into TaskRunner, remote
attestation, a historical target calibration, or completion of P07. The real Docker smoke (run 40377319964) and private legacy transplant
(run 40377315871) subsequently completed successfully on runtime
`1146f073d642ca259544f95dea27ce1d0bc4bb75`. Downloaded artifact digests
were independently checked: `check-spec-real/` and `legacy-transplant-real/`
under `validation/p07-budget20/`. The former confirms all three synthetic
PASS/assertion-FAIL/required-SKIP cases with cleanup; the latter confirms
private Python 3.6 stdlib/ELF identity and unchanged target source. Neither
result establishes historical target calibration or full-loop integration.

## Implemented API and exact trust boundary

`CheckSpec` is an immutable host-owned description of a task definition, target
base, edited source revision, immutable image reference, fixed interpreter,
runner, complete ordinary-test file obligation, target root and bounded
resources. Its canonical SHA256 binds these fields. Constructing one does not
establish that an image or workspace was independently admitted.

`run_check_spec(spec, workspace, cancel_check=...)` requires an already admitted
private workspace and a host-created specification. It checks the source
revision, existence and link confinement of every declared test file before
execution. It constructs Docker configuration from the specification and calls
the existing `run_python` lifecycle using `host_spec` and a per-invocation nonce.
Direct `run_python` calls with a host spec repeat this admission and refuse
local mode, differing images/profiles/arguments, output or deadline expansion.

The Docker command uses the exact admitted interpreter and mounts the workspace
at the specification's `/workspace` or `/testbed` root. It applies the actual
specified resource values, including smaller values than the existing checks
profile. Network-none, read-only container root, non-root UID, dropped
capabilities, no-new-privileges, no automatic pull, private bounded tmpfs and
credential-clean environment remain enforced. It reuses cancellation, bounded
stream capture and owned process/container cleanup. An unconfirmed cleanup
raises; it cannot return a successful check receipt.

The pytest bootstrap imports the installed checker before exposing target
imports. It records interpreter/checker identity and the collection of every
declared file. Zero collection, missing files, skipped tests, failed reports,
an unfinished session, nonzero exit, timeout or truncated output cannot produce
`check_passed=True`. The outer host also compares source revisions before and
after the check. That comparison excludes admitted ignored/generated files;
it is not a claim of byte-for-byte equality of every filesystem object.

This path currently refuses native SymPy, Python before3.8 and the known
historical pytest repository self-test task IDs. Those need distinct runner and
source-import identity contracts; an installed pytest checker is not the target
pytest source. All four existing runtime consumers still use their previous
execution paths until explicitly integrated.

`CheckReceipt.verified_success` is always false. The `runner_receipt` is supplied
by a target Python process; hostile project tests or plugins can tamper with
their interpreter and output. The nonce prevents accidental cross-invocation
mixing, not adversarial forgery by a process that sees its own arguments.
`check_passed` describes bounded ordinary-test execution under these assumptions.
Independent hidden requirement grading remains a separate fresh oracle action.

## A fresh source-bound spec after every admitted edit

A spec from before an edit must become stale. Reusing its `source_revision`
after a patch is supposed to fail admission. The solution is a host factory
that derives a fresh invocation spec from a previously admitted, immutable task
obligation and the current private snapshot; it is not disabling the revision
comparison or accepting a revision proposed by the model.

The future factory must enforce these invariants:

1. Freeze task definition, source ancestry, original image/donor/profile
   receipts, interpreter, runner, import-layout policy, ordinary-test inventory
   and content hashes, maximum resources and final verification floor before
   inference. The agent cannot edit these inputs or substitute a different
   task's environment.
2. Admit an edited snapshot through the current changeset/scope machinery.
   Protect ordinary tests and runner infrastructure. Check their bytes against
   the frozen obligation before computing the new source revision; simply
   hashing changed test files would otherwise authorize the agent to weaken
   its own tests.
3. Compute the revision from that same admitted snapshot on the host. Construct
   a fresh spec retaining all immutable task/environment obligations. Recheck
   source identity at process admission and after completion. A concurrent edit
   invalidates the result rather than allowing a newer factory invocation to
   relabel an older receipt.
4. Bind the immutable obligation digest into approval/envelope/lineage. Bind
   each concrete invocation spec digest to its step attempt and checked source
   revision. Preserve the distinction between a stable approved task obligation
   and a new per-snapshot execution spec; do not require approval of each ordinary
   edit merely because its source revision changes.
5. Use the same factory and receipt validation for model-requested checks,
   post-edit checks, automatic step verification and the final publication gate.
   A model-selected subset may only narrow an intermediate check within the
   admitted ordinary tests. It cannot narrow or replace the final host obligation.
6. Constrain each invocation by the original task's non-renewing deadline,
   remaining repair allowance and resource/output ceilings. A freshly computed
   source hash or replacement subtask must not renew any global limit.
7. Persist failure, cancellation, timeout and unknown cleanup before allowing
   repair/replan or resume. Never reuse an earlier successful receipt after a
   source change or automatically replay an uncertain submission.

The current `CheckSpec` has file paths and a source revision, but not a separate
frozen ordinary-test content manifest or an envelope-integrated host factory.
Those are remaining implementation requirements, not properties established
by the current dataclass. Its source-ancestry fields are host-admitted identities;
the standalone boundary does not reconstruct Git ancestry from a filtered
workspace without `.git`.

## Necessary evidence before full-loop claims

The synthetic Docker smoke must first retain the actual pinned image identity,
interpreter, collection, source revisions, expected PASS/assertion-FAIL/SKIP
behavior and confirmed cleanup. A synthetic qualification still proves no
historical package imports, protected requirement success or TaskRunner journey.

After the factory and all four consumers are connected, one public built-in
TaskRunner journey must demonstrate source-bound check failure, bounded repair
or replan, fresh checks, the complete final gate and publication. Cover stale
source/test inventory, model narrowing attempts, timeout/cancellation,
uncertain cleanup and resume without replay. Keep hidden oracle test patches
outside both model context and agent containers. Independently grade the
published candidate using a fresh calibrated oracle before reporting verified
requirement success. Full P07 additionally needs the matched comparison,
holdout and ablations; this boundary does not replace any of them.
