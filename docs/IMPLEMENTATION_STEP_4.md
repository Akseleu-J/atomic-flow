# Package 4: reproducible Linux/Docker CI

18 October 2026. Continues packages 1–3 and their uncommitted working-tree changes.

Decision: edit and run CPU tests locally; use GitHub Actions Ubuntu for actual Docker acceptance.
The user's HP 15-db1xxx has Ryzen 3 3200U (2 cores / 4 threads), 3.4 GiB usable RAM,
about 0.4 GiB available at inspection and 54.4 GiB free disk. Virtualization is enabled.
Docker Desktop installation is unsuitable for the current memory budget; no software was installed.
Docker's published requirement: https://docs.docker.com/desktop/setup/install/windows-install/

Added:

- Python 3.11/3.12 CI jobs and a separate real Docker acceptance job.
- Fixed-SHA checkout/setup/upload actions, read-only repository permission, no persisted Git credentials.
- `--require-docker`: missing/unavailable Docker cannot produce a passing CI result.
- Real matching-pytest, successful publication, failed-edit rollback, timeout and cancellation container tests.
- Nonroot caller uid/gid in Docker, preserving access to private Unix workspaces without chmod or root.

Previous Codespace run (16 October, separate source snapshot): 154 CPU tests passed,
6 skipped; 17 root tests passed. Actual Docker: 4 passed, 2 failed due to inaccessible
workspace permissions with fixed uid 65534. That run does not validate the corrected UID/GID code.
The Codespace allowance is exhausted; do not start more sessions.

Current Windows run: **170 passed, 9 skipped**, `validation/ci-local-20261018.txt`.
Docker tests intentionally skipped locally; Linux/real Docker results must be taken from the CI run
for its exact source commit. Ruff on affected execution/test/validation modules, compileall and
Git diff checks passed. Dependencies and base image are still floating; runtime image ID and
installed dependency versions are recorded for each CI job.

Pending at preparation: remote CI execution and its artifacts. This package does not close
planner/replanner cost accounting, hard per-call task budget or live model quality validation.
