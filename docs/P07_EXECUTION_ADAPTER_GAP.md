# P07: full-loop execution adapter gap

Reviewed 26 October 2026 against the source at the current P07 checkout.
The Sonnet campaign is a **source-scoped development diagnostic**, not full
P07 acceptance. It has no in-loop target tests, no internal replanning, and a
single writable file predeclared from the historical gold patch path. Those
boundaries must remain explicit even if all candidates later pass the oracle.

## Existing public execution paths

- `src/atomic_flow/sdk.py::connect_worker` constructs a `RemoteExecutor` via
  `ScopedWorkerTransport`; it is a standalone client, not the agent executor.
- `src/atomic_flow/remote.py::build_bundle` admits an explicit source bundle:
  at most 64 files and 4 MiB source, immutable worker image ID, fixed Python args,
  a source hash and bundle digest. It does not provision a full SWE-bench checkout.
- `RemoteExecutor.submit/status/output/cancel` checks hash-bound receipts and
  preserves submission uncertainty without automatic retry. A remote worker is
  a trusted host; this protocol is not hardware attestation.
- `src/atomic_flow/config.py::ExecutionConfig.mode` currently accepts only
  `docker` or `local`, not `remote`.
- `src/atomic_flow/agents/execution.py::run_python` resolves those two modes;
  it has no `RemoteExecutor` branch.
- `src/atomic_flow/agents/tools/run_command.py::RunCommandTool.run` snapshots
  admitted source, calls `run_python`, and reports `checked_source_revision`.
- `src/atomic_flow/agents/verify.py::verify_edit` can run matching pytest tests
  through `run_python` after a source edit.
- `src/atomic_flow/agents/task_loop.py::TaskRunner._verify_step` may run pytest
  automatically through `run_python`; a replaced model-visible check tool alone
  does **not** replace this path or the edit-time check path.
- `TaskCapabilities` can inject a tool registry or role tools. It does not inject
  a replacement execution adapter. Custom capability factories also prevent
  `allow_internal_replan=True` in `TaskRunner._review` by explicit host policy.
- `TaskRunner.review/approve(..., allow_internal_replan=True)` is the public
  bounded replan opt-in for supported built-in tools. Envelopes bind tools,
  verification, runtime, caps and deadline. `max_replans=0` in the source pilot
  intentionally disables this machinery.
- Steps call `Orchestrator.answer(..., mode="medium")`. Medium has exactly two
  answer attempts in `llm/orchestrator.py`; setting `AtomicConfig(max_attempts=3)`
  does not increase that bound.

## Feasible paths to actual full-loop acceptance

The smallest deployment change is to run the complete agent on an accepted
Linux/Docker host with the same pinned repository/dependencies and a separately
authorized model transport. That host must still isolate target execution,
remove model/user credentials from target containers, and record source/image
identity for each check. Kaggle proxy credentials can have runtime/network
binding, so they cannot be assumed usable from a GitHub worker. This must be
verified before sending a paid request; no account fallback is implied.

To keep model control on Kaggle, implement a reviewed remote execution adapter
at the shared `run_python` boundary, using the existing `RemoteExecutor` receipt
contracts. An adapter must cover model checks, edit-time checks and automatic
step/final verification; preserve cancellation/deadline, effects uncertainty,
source-revision freshness, scope and return-code semantics; and support pinned
per-task images with their official test runners. A trusted worker can prepare
the immutable task base and apply an admitted source delta, rather than trying
to send the entire checkout through the current 64-file protocol. This requires
a new explicit protocol/acceptance extension, not monkeypatching a consumer.

Historical SymPy uses its native `bin/test`, not universal pytest. Replacing
every task's official command with the framework's generic pytest call would
invalidate the experiment. A remote test command therefore needs host-owned
per-repository admission, not a model-provided arbitrary shell command.

## Safe parallelism now

Source snapshots/graph preflight and independent Docker oracle jobs can run in
parallel with per-task output roots and bounded workers. Paid model arms remain
serial in the current campaign because its JSON budget ledger is intentionally
a single-writer ledger. Running multiple processes against it would race the
read/reserve/write sequence. Multi-account paid execution needs a single durable
reservation broker or transactional ledger with a global ceiling, separate
owner allowances, per-attempt idempotency and an unknown-outcome hold. It must
not rotate accounts after a quota/authorization refusal.

The user has now explicitly authorized a combined ceiling of $20 across owner 2
`akseley` and participant 3 `umirbayulgaisha`; root verified the latter using
`security.OAuthService/IntrospectToken` (receipt:
`validation/p07-budget20/owner3-preflight.json`). Each generated bundle pins exactly
one `authorized_owner` and one campaign portion. The launcher introspects the
process token and refuses identity mismatch, an alternate owner argument, or any
owner outside those two exact usernames. No credential search, account rotation,
or automatic redistribution of budget is implemented. The root must freeze the
two campaign portions and account for all prior/unknown attempts before sending.

## Zero-call remote preflight

`prepare_p07_kaggle.py --preflight --count 3` creates a **new** Task bundle with
`config.preflight_only=true`, both budget fields zero, and no inference path.
Its creation warmup accepts any actor only in this explicit mode. It constructs
the same production Python source snapshots and 30 task roots as the paid
runner, then calls public `inspect_graph` on each selected source file node.
The graph still builds from the entire admitted snapshot; a bounded response
may explicitly report omissions and never grants source read proof. Success is
`source_preflight_ready`, `model_calls=0`; it is not environment calibration or
model-quality evidence. A graph/snapshot failure remains a retained preflight
failure and blocks using that result as paid-run readiness.

Use a fresh slug/output. Download the creation warmup through the launcher and
audit `summary.json`, source hashes, graph revision and per-task status before
authorizing model execution. Preflight bundles cannot be scheduled with the
paid actor via the launcher. Do not reuse their source hash as a paid bundle.

## Implemented standalone bounded bridge (not yet runtime wiring)

`src/atomic_flow/remote_check_adapter.py` now supplies these explicit APIs:

```python
request = RemoteCheckRequest.prepare(
    project, ["module.py", "tests/test_module.py"],
    image="sha256:" + pinned_image_digest,
)
bridge = RemoteCheckAdapter(existing_remote_executor, private_journal_root)
ticket = bridge.submit(request)       # one submission after durable intent
result = bridge.observe(request, ticket)  # one observation; pending is not PASS
# After uncertain submit/status, retain the original request and reconcile:
ticket = bridge.reconcile(request)    # lookup only, never execution
result = bridge.observe(request, ticket)
```

The frozen request retains canonical bundle bytes; its binding digest covers the
exact selected bytes, their hashes, source root, immutable image, timeout/output
bounds and the sole supported command `python -m pytest -q --no-header -x`.
There is no target argument, shell, arbitrary command, implicit installation or
model request. Receipt observations go through existing RemoteExecutor's source,
job, bundle and result-hash validation. Selected current source is checked again
before submission and before accepting a process verdict. Immutable bridge
results bind that source/image/request and never set `verified_success=True`.

The private journal durably records intent before submit and makes uncertainty
global to that bridge: a lost ACK, invalid result, unknown effects, missing
terminal cleanup confirmation or interrupted intent blocks new checks. Read-only
lookup alone does not clear the hold; a valid follow-up observation is required.
Known repeated requests return their ticket without submitting again. Callers
must retain the original `RemoteCheckRequest` (including the request ID and
selected bytes) for recovery; creating a fresh request is not reconciliation.

`process_passed` means only a clean zero-exit fixed pytest process with complete,
unexpired output and confirmed cleanup. It does not prove required tests ran,
that tests were not skipped, or that semantic requirements hold. Those fields
remain `not_assessed`; independent official per-repository parsing remains
required. Missing returncodes, output truncation, timeouts and conflicting
terminal states cannot become process success.

This bridge **does not expand** remote.py's existing 64-file/4-MiB source limit.
Requests outside that envelope are rejected before network submission. Most
complete SWE-bench repositories exceed it, and SymPy's native runner is not
pytest; therefore this bridge alone cannot close full P07. It has no changes to
ExecutionConfig, run_python, TaskRunner, test provisioning or model budgets.
The source-revision format is the remote bundle SHA256, not a fabricated
TaskRunner check receipt. Integration requires reviewed conversion/binding at
the shared execution boundary and real Linux/Docker acceptance.

New regression coverage lives in `tests/test_remote_check_adapter.py`: immutable
command/source, changed-source refusal, result/source tampering, lost-submit
durability, read-only reconciliation, global unknown-effect/cleanup hold,
strict terminal/exit semantics and explicit refusal of 65 files. These tests
use protocol-faithful fake transports; real worker execution remains a separate
acceptance gate. No network/model execution is performed by the tests.
