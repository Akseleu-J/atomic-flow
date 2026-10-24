# Package 3: managed execution and cancellation

Implemented over the uncommitted packages 1/2 on HEAD `820fc83`.

- Shared bounded-output executor for run_command, matching pytest and TaskRunner pytest.
- Default Docker execution with no implicit host fallback; explicit trusted local mode.
- Clean child environment, deadlines, cancellation polling and process cleanup.
- Independent durable task cancellation marker, checkpoints on cancellation and publication recovery.
- Single-runner OS task lease; cancellation checks around synchronous model calls.
- Task budget checked before the next step (previously only after failure).
- CPU Colab workflow, reviewed source-only ZIP with hashes, install/probe and real container test.

Current evidence is in `docs/validation/step3-final.txt` and `step3-root.txt`.
Windows: **151 passed, 4 skipped** + **17 passed** separately; Ruff, compileall and diff-check pass.
Real Colab probe: restricted containers unavailable because cgroups are read-only, including
after a no-bridge/VFS daemon successfully started. See `validation/step3-colab-preflight.md`.
Full source tests have not run in Colab; only the live environment probe has.
See `EXECUTION.md` for commands, operational differences and limitations.

Not completed: task-wide planner/replanner budget accounting and per-call hard task cap;
provider-side cancellation; Windows Job Object process containment; locking of manual CLI recovery;
release image/dependency pinning; real-model quality benchmarks. Local-mode passing tests
do not establish Docker isolation or production readiness.
