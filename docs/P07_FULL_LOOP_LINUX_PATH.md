# P07: concrete full TaskRunner path on a Linux Docker host

Read-only source review, 26 October 2026. This is an implementation path, not
an implemented adapter, live-model result, or production acceptance. The current
source-only campaign still cannot satisfy full P07.

## What the current executor actually supports

| Requirement | Source evidence | Current result |
| --- | --- | --- |
| Choose an exact task image | `config.py::ExecutionConfig` lines123–128; `agents/execution.py::docker_command` and `run_python` | Yes: `image` is configurable and passed to Docker. Provisioning is separate: the command uses `--pull=never`. A host must first pull/inspect an official immutable digest or independently admit a derived dependency image. |
| Choose the historical interpreter | `agents/execution.py::docker_command` | No field exists. The final command is `image, "python", *_trusted_checker_args(args)`. Image PATH decides which Python runs; the code does not activate `testbed` or select `/opt/miniconda3/envs/testbed/bin/python`. |
| Execute against the edited source | `agents/execution.py::docker_command` | It binds the supplied project directory at `/workspace` and sets that cwd. It does not replace `/testbed`. Historical editable installs may still reference `/testbed`; source identity must be proved, not inferred from a successful exit. |
| Carry an admitted complete task workspace | `agents/changeset.py::ChangeSet.create`, `_files`; `project_resources.py::ProjectLimits` | There is no remote64-file/4-MiB cap on this local Docker bind. TaskRunner snapshots admit up to10,000 files,128MiB total,16MiB/file,100,000 traversal entries and depth64. All required ordinary tests/data/configs must actually fit and survive admission. |
| Preserve every Git checkout byte | `agents/changeset.py::_files` lines181–190; `tree/defaults.py::DEFAULT_IGNORE` | No. `.git`, private credentials, most hidden files, caches, lock-pattern entries and unsafe links are excluded or refused. An admitted workspace is not automatically a complete historical checkout. No setup/test command may silently depend on omitted bytes. |
| Run native SymPy checks | `agents/tools/run_command.py::RunCommandArgs`, `ALLOWED`; automatic check consumers below | No configuration path exists. The model tool allows only pytest/ruff/mypy; the host test consumers explicitly construct pytest arguments. |

The existing `checks` Docker profile is **1GiB RAM,2CPU,128PIDs and128MiB
tmpfs**. `read-only` is512MiB,1CPU,64PIDs and64MiB tmpfs. Both have
network=none, a read-only container root, all capabilities dropped,
no-new-privileges, a non-root identity, no automatic image pull and a private
`/tmp`. The checks workspace bind is writable; the read-only profile makes it
read-only too. These bounds are fixed host-owned `EXECUTION_PROFILES`, not
model-adjustable resource arguments. The oracle's6GiB/256PID containers are a
different contract and are not evidence that a full agent check fits this one.

`run_python` bounds output per stream (`ExecutionConfig.output_bytes`: default
16KiB, admissible1KiB–1,000,000bytes), preserves truncation/timeout information,
owns process cleanup, and refuses uncertain Docker removal. `clean_environment`
does not forward provider keys, PYTHONPATH or the agent's private HOME. None of
those controls should be weakened to obtain benchmark success.

## All four consumers must use the same reviewed target-test runtime

1. `agents/tools/run_command.py::RunCommandTool.run` constructs `-m pytest`,
   ruff or mypy; optional targets must remain confined paths. It snapshots/checks
   source identity and calls `run_python` with a120-second timeout.
2. `agents/verify.py::verify_edit` compiles bytes without execution, then runs
   matching pytest files through `run_python`, normally120seconds. Matching
   searches are under `tests/`; absence of matches explicitly gives syntax-only
   verification, not proof of target tests.
3. `agents/task_loop.py::TaskRunner._verify_step` accepts verification kinds
   none/py_compile/pytest. Without an adequate fresh tool check after the edit,
   its pytest branch performs a full pytest check with a180-second timeout.
4. `agents/completion.py::assess_completion` lines73–107 independently performs
   final full pytest when tests are present, final_check requests pytest, or a
   completed step had that obligation. Its timeout is180seconds and its result
   gates publication. Replacing only the model-visible tool misses this gate.

`execution.py::_trusted_checker_args` deliberately starts pytest/ruff/mypy with
`python -I` and imports the installed checker before adding the workspace. This
protects ordinary checker discovery. Historical pytest-self-development instead
requires pytest and `_pytest` from the exact edited target checkout; blindly
keeping an installed modern checker is not valid for those corpus tasks. The
official independent evaluator already checks `/testbed` import identities for
those tasks, but that does not wire the live TaskRunner path.

For SymPy, calling the native `bin/test` cannot be presented as an unchanged
pytest check. `TaskRunner._check_failure` currently recognizes only
syntax/pytest/ruff/mypy and interprets exit1 as a deterministic check failure.
Step verification, planner/task schemas, failure receipts and the replan envelope
must describe the actual host-owned runner consistently. Native test exceptions,
collection failures, timeout and missing output must not become generic PASS.

## Smallest honest implementation sequence

1. **Keep the complete agent on one accepted Linux/Docker host.** This avoids
   implementing remote64-file protocol expansion merely to run local checks.
   Use the exact framework source/dependency identity and an independently
   authorized model transport. Do not assume a Kaggle-issued proxy credential
   works from GitHub; verify transport binding without paid inference first.
   No model token, user HOME, Docker socket or private oracle root enters a
   target container or its mount. The current Kaggle creation failures remain
   a separate external-service blocker.
2. **Provision a base workspace and a separate hidden oracle.** Materialize
   the exact base commit and ordinary base tests/data on the host using a
   source-only export; never run repository setup/hooks on that host. Bind a
   reviewed per-task image/digest and its historical dependencies. Keep gold,
   protected test_patch, negative controls, oracle receipts and calibration
   failure details outside both model-visible source and container mounts.
   Audit official-image auxiliary files too: image provenance alone does not
   establish that hidden evaluation data are absent. Protect base tests and
   test-runner infrastructure from agent edits.
3. **Inventory admission before inference.** Run public graph construction,
   `ChangeSet.create` and exact source/fixture inventory under existing host
   resource limits. Prove required non-Python data and configs are present;
   report omitted/unsafe inputs. Do not reuse the production-Python-only pilot
   snapshot as a complete target workspace. Refusal is an infrastructure
   result; no silent subset or resource increase is allowed.
4. **Add an explicit host-owned target-test specification at the shared
   execution boundary.** Bind task/base/image identity, fixed interpreter,
   stable target root, allowed runner/arguments, required test paths, resource
   profile, deadline and source revision. A per-task profile is not an arbitrary
   shell command. The mount/import layout must make the exact checked edited
   source visible at the paths used by historical editable installs. Provision
   the environment before the run; do not pip-install target source on the host
   or dynamically repair dependencies during a model check.
5. **Wire that specification through all four consumers.** Reuse current
   cancellation, bounded output, clean environment, durable effects uncertainty
   and cleanup. Preserve source snapshots and checked-source revisions. Allow
   model target selection only within the admitted ordinary tests. Host final
   verification must still run its full declared regression obligation. Give
   pytest-self tasks explicit target-runner discovery and SymPy its native
   command; do not hide either behind a misleading pytest label or a Python
   wrapper that lies about the command actually run.
6. **Use public built-in TaskRunner replanning.** Leave `capability_factory`
   unset: `_review` explicitly refuses internal replans with injected
   capabilities. Review and approve with `allow_internal_replan=True`, a fixed
   configured backend, enforced finite task cost/call limits, a positive bounded
   replan count, fixed routing and a non-renewing deadline. `envelope.py`
   binds config/runtime/policies/verification floor. The new target-test
   specification and actual verification kind must be included in that binding.
   Replacing tools or monkeypatching imported `run_python` functions would not
   be full public-path acceptance.
7. **Prove one complete real journey before paid matched pairs.** On that
   Linux host demonstrate prepare→plan/subtasks→approved source edits→real
   check failure→typed bounded repair/replan→fresh checks→final gate→diff and
   publication, including cancellation/timeout and uncertain-cleanup refusal.
   Record ordinary-test runner/import/image/source identity. Independently
   grade the published candidate in a fresh calibrated oracle with the hidden
   test patch, preserving original bytes and logs. A TaskRunner publication is
   still not an independent requirement-success verdict.

This sequence is smaller than a new remote execution service, but it still
requires a real core integration. Image selection alone does not provide
historical interpreter selection, native command semantics, target-import
identity, or full-loop repair. If a task cannot fit the fixed executor profile,
an explicitly reviewed new host profile and acceptance evidence are required;
the existing1GiB/128PID profile cannot be described as6GiB/256PID by changing
benchmark prose.

## What can be attempted without that core change

A fully inventoried pytest-based subset may be used for an exploratory public
TaskRunner journey only after its image/interpreter/import/layout preflight
actually succeeds under the current bounds. It cannot establish all30-task
acceptance, because SymPy native checks and historical pytest-self runner
identity remain unsupported. Source-only Sonnet diagnostics, standalone remote
bridge fake-transport tests, and successful independent oracle jobs each retain
their existing narrower evidence scope.

No code, tests, local subprocesses, model calls or CI runs were performed by
this architecture review. See `P07_EXECUTION_ADAPTER_GAP.md` for the current
standalone remote bridge boundary and its fixed pytest64-file/4-MiB protocol.
