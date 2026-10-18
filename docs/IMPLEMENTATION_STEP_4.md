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

## Confirmed GitHub Actions result

Run: https://github.com/Akseleu-J/atomic-flow/actions/runs/39102109420

Tested source commit: `5a4fbc5d5cdc3b101d2558046a964586c06b0c22`.
All three jobs succeeded: Python 3.11, Python 3.12 and Real Docker acceptance.
The Docker job recorded **156 passed, 6 skipped** in the standard CPU suite,
**17 passed** root tests and **6 passed** opt-in real container tests.
Its `container=PASS`, zero model calls and image ID are stored in
`validation/ci-20261018/validation-5a4fbc5d5cdc3b101d2558046a964586c06b0c22/`.
`tested-commit.txt`, `dependencies.txt`, Docker version/build logs and results.json bind the evidence
to that commit and execution environment. This verifies the tested boundaries and fixtures,
not resistance to all container escapes or compatibility with every target project's dependencies.

This package does not close
planner/replanner cost accounting, hard per-call task budget or live model quality validation.
