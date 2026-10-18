# Project context for subsequent work

Read `docs/PROJECT_CONTEXT.md` first. It records architecture, entry points, validation evidence, known gaps and the source commit.
Use `docs/project-index.json` for file hashes, imports, symbols and line ranges; then read only the modules relevant to the task.
The index is a snapshot, not current truth: check `git status` and changes since its `source_commit` and refresh affected sections after implementation.
Refresh the static inventory with `python tools/update_project_index.py` (tracked files plus new Python files in src/tests). It records dirty state and does not call models or import benchmark fixtures.
The first implementation package is recorded in `docs/IMPLEMENTATION_STEP_1.md`; consult it before treating original review findings as still open.
The current implementation also includes `docs/IMPLEMENTATION_STEP_2.md`: task workspaces, source freshness, journaled publication/recovery and its limits. Staging is not an execution sandbox.
Package 3 is recorded in `docs/IMPLEMENTATION_STEP_3.md` and `docs/EXECUTION.md`: default Docker checks, explicit local mode, cancellation, task lease and Colab validation. Keep CPU tests separate from real container evidence.
Package 4 (`docs/IMPLEMENTATION_STEP_4.md`) adds GitHub Actions for Python and real Docker checks; consult exact CI commit/results. Codespace allowance is exhausted. Do not install Docker Desktop on the 3.4 GiB RAM laptop.
Package 5 (`docs/IMPLEMENTATION_STEP_5.md`) adds durable task accounting, shared daily reservations and recovery leasing. Do not call estimates guaranteed provider billing caps. Validation uses mocks/scripts only.
Do not infer current model quality from historical `METRICS.md` results. See the context document for actual local checks.
