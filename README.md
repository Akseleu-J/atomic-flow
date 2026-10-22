# atomic_flow

Project-aware LLM development platform: indexes source code, selects relevant context, answers with citations,
**checks the answer deterministically against the context it was given**, and measures quality with free retrieval
metrics plus local and Kaggle-hosted benchmarks. Kaggle Model Proxy, OpenAI-compatible and Anthropic backends.

```bash
pip install -e '.[api,kaggle,benchmarks,dev]'
pytest                                   # no network, no keys needed (scripted fake backend)
atomic init --project && atomic tree build
atomic benchmark retrieval               # FREE: ranking quality, zero model calls
atomic ask "What does Session.refresh do?" --explain
```

Kaggle: `kaggle auth login`, `atomic kaggle init --yes`, `atomic kaggle status`, then
`atomic benchmark run --model <available-model> --repeats 3 --yes` (check `atomic kaggle quota` first).

## What changed in 0.2

See `REVIEW.md` (problems found in 0.1 and how each was fixed) and `docs/architecture.md`.
Tree format is v2: run `atomic tree build` once after upgrading.

## Safety defaults

* A cloned repo's `.atomic_flow/config.yaml` cannot set `backends`/`default_backend` until you run `atomic trust`.
* `atomic ask` edits require `--approve-edits` (or `ATOMIC_FLOW_ALLOW_EDITS=1`) **and** a permitted policy decision.
  Trust alone never enables edits; `--yes` answers approval prompts but does not enable edits for `ask`.
  Starting/resuming `atomic task` enables edits for that task, subject to policy and its approval callback.
* Tool permissions are enforced per role by the dispatcher. Every call, including a denial or invalid arguments,
  records `result_summary.outcome` (`success`, `error`, `denied`) and verification evidence.
  A task step fails on any tool error/denial; `py_compile` steps require an applied, syntax-checked edit.
  `pytest` steps require a successful full-suite run after the last edit (the runner performs it if needed).
  These checks establish execution evidence, not semantic correctness of the entire requested change.
* `atomic up` binds to loopback; network binds need `ATOMIC_FLOW_SERVER_API_KEY` (>=16 chars) and `ATOMIC_FLOW_PROJECT_ROOT`.
* Run artifacts (`~/.atomic_flow/runs/`) contain prompts and source snippets: `artifacts.save: false` disables them,
  `atomic runs prune` applies `artifacts.keep_days`.
* `budget.daily_limit_usd` is enforced locally (calls without a known cost are not counted; set `price_in_per_mtok` /
  `price_out_per_mtok` on a backend to make cost known).

## Task workspaces and recovery

`atomic task` executes steps and verification in a separate filesystem workspace under
`~/.atomic_flow/changesets/<id>/workspace`. It publishes approved, verified file revisions only after all steps pass.
An external change to the original project, an unapproved workspace change, or a changed verification hash blocks publication.
Failed steps restore their workspace checkpoint before replanning. A failed task leaves the original source unchanged unless
publication was interrupted or recovery encountered a conflict; the journal identifies that state.

`atomic task --show TASK_ID` displays the workspace path. `atomic task --recover TASK_ID` recovers an interrupted publication
**offline**, without constructing a backend. Recovery preserves files whose contents have since changed externally.
Old task checkpoints without a workspace cannot be resumed partway through, and interrupted running steps require review.

This is recoverable filesystem staging, not a sandbox, Git worktree, or an atomic multi-file filesystem operation.
Quality commands and pytest use Docker by default, with an explicit trusted local mode.
See [execution and Colab validation](docs/EXECUTION.md) for setup, cancellation, process limits and current evidence.
The real Colab probe could not start the restricted container because cgroups are read-only.
GitHub Actions now runs Python 3.11/3.12 checks and a required real Docker acceptance job;
[the first successful run](https://github.com/Akseleu-J/atomic-flow/actions/runs/39102109420)
passed all six container integration checks, including task publish/rollback and cancellation cleanup.
The workspace omits Git metadata, environments/caches, private atomic state, and most hidden files; projects depending on
those files or Git metadata need a dedicated workspace/environment strategy before use.
`atomic ask --approve-edits` continues to edit directly with per-edit backup/hash checks.

Read/edit tools reject stale source and symlink/junction paths. Edits refresh the tree and preserve existing node aliases;
`read_node` uses explicit pages (`offset`, `next_offset`) for long definitions. Undo requires the original project and
the exact expected current file hash; older backups without that metadata are not automatically restored.

`atomic task --cancel TASK_ID` signals the running worker without racing its task JSON saves.
Cancellation stops managed checks and interrupts local waiting for built-in HTTP model adapters.
Remote inference may continue: an unknown charge retains its reservation and blocks replay.
A fully received response is accounted before cancellation. Simultaneous runners for the same task are rejected.

Planning, replanning and repair calls share durable task budget events. `--dry-run` saves
the plan and planning cost. A budget pause restores the current step checkpoint; known-cost
work can resume after adjusting its limit. Unknown/in-flight charges block automatic replay.
Offline `--recover` uses the runner's task lease.

With a USD limit enabled, configure backend `price_in_per_mtok` and `price_out_per_mtok`.
Use 0/0 only for a confirmed free backend. Task calls default to a finite output limit,
no transport retries and at most 100 model calls. Reservations use declared prices and an
input allowance; they cannot guarantee provider billing. See [task budgets](docs/IMPLEMENTATION_STEP_5.md).

Role requests now have a configurable serialized-message byte limit. Older tool bodies can
leave the prompt with explicit provenance/proof placeholders; original instructions and recent
results remain intact. If these cannot fit, no model request is sent. Repeated identical tool
requests without a source revision change stop before another tool execution. See
[history limits and architecture review](docs/ARCHITECTURE_REFERENCE_REVIEW.md).

The selected twelve improvements add full-request admission, scoped project instructions,
actual read coverage, bounded text search, hash-bound file operations, layered tool policies,
explicit user questions, stored result pages and versioned session/task observations.
Use `atomic session list|show|export|recover-tail` to inspect journals without replaying actions.
See [package 7](docs/IMPLEMENTATION_STEP_7.md) for usage, validation and limits.

## Live events and Python SDK

`atomic ask "Explain the refund rule" --mode simple --events` emits version 1 JSONL
observations and a terminal result/error. Ctrl-C requests cancellation. Slow consumers
receive explicit gap counts; oversized payloads are marked omitted. These are execution
events, not provider token deltas.

```python
from atomic_flow.sdk import start_answer

handle = start_answer(config, project, backend, "Explain the refund rule", mode="simple")
for event in handle.events():
    print(event.model_dump_json())
result = handle.result()
```

Another thread can call `handle.steer("Answer briefly")` or `handle.cancel()`.
Guidance applies at safe boundaries and cannot grant permissions or rewrite an action
already in progress. Host-supplied Python backends/callbacks must cooperate with cancellation.

The optional API exposes `POST /v1/runs`, `GET /v1/runs/{id}/events` (SSE),
`POST /v1/runs/{id}/control` and `GET /v1/runs/{id}`. Existing API-key/project-root
checks apply; stream disconnect requests cancellation. Handles are bounded and ephemeral,
not durable jobs or executable session replay. See [controls, SDK and limits](docs/IMPLEMENTATION_STEP_11.md).
