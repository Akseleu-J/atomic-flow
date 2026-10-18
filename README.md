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
The real Colab probe could not start the restricted container because cgroups are read-only;
container isolation acceptance still requires a suitable Docker host.
The workspace omits Git metadata, environments/caches, private atomic state, and most hidden files; projects depending on
those files or Git metadata need a dedicated workspace/environment strategy before use.
`atomic ask --approve-edits` continues to edit directly with per-edit backup/hash checks.

Read/edit tools reject stale source and symlink/junction paths. Edits refresh the tree and preserve existing node aliases;
`read_node` uses explicit pages (`offset`, `next_offset`) for long definitions. Undo requires the original project and
the exact expected current file hash; older backups without that metadata are not automatically restored.

`atomic task --cancel TASK_ID` signals the running worker without racing its task JSON saves.
Cancellation stops managed checks and is observed around model calls and publication. An in-flight synchronous
model call completes or times out before cancellation is observed. Simultaneous runners for the same task are rejected.
