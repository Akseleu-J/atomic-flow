# Import review: execution and installed distribution

Reviewed on 2026-10-24 against read-only external checkout
`C:/Users/omirb/.zcode/workspace/7a9e7cc1-327f-4133-a7af-0ccfe5ee24e8/atomic-flow`,
HEAD `c8820ff`. Source changes were imported by the coordinating agent after
backing up the destination working tree. This report covers execution/jobs,
Windows lifetime management, wheel CI, packaging metadata and wheel smoke.

## Decisions

Adopt the owned Windows Job Object for Python checks: assignment occurs in a
trusted isolated bootstrap before project code; no breakaway is configured;
the retained host handle uses kill-on-close. Termination confirmation queries
the job accounting active-process count until it is zero. This is process
lifetime management, not filesystem/network isolation. The 30-second bounded
confirmation change in `9fac03f` is reasonable and remains; expiry/query failure
must continue to report uncertainty.

Reject the parent-exit argument in `49acacd` and the taskkill-status-128 argument
in `9fac03f`. `_kill_tree` is also used for generic MCP host process cleanup.
Termination of one Popen says nothing about a surviving descendant after a
tree-helper failure. The imported version returned normally after a simulated
helper timeout and fallback parent kill while a modeled descendant stayed
alive. The retained standalone AST probe starts no OS processes; before/after
JSON is in `validation/import-20261024/execution-mock-{before,after}.json`.
The integrated fix preserves uncertainty for every helper exception/nonzero
status, including 128, even when the parent has exited. Regression cases cover
exited parent plus statuses 1/128, helper timeout, and a stubborn parent.

Additional fake-API regressions require WindowsJob accounting observations
2 -> 1 -> 0 before success, and reject query failure or a still-active tree
at the confirmation deadline. No detached uncontrolled descendants are needed
for these tests. The first scoped run after restoring uncertainty produced
30 passed / 3 skipped / 4 MCP cleanup failures; retained log:
`execution-tests-before-mcp-ownership.txt`. The failures exposed the underlying
generic helper deficiency rather than justifying the rejected parent-exit proof.

Windows MCP and LSP now use the same owned WindowsJob lifetime. The isolated
bootstrap joins the job before launching the exact already-approved absolute
host command; no new command authority is introduced. The bootstrap accepts the
complete argv instead of assuming its target is another Python invocation.
Generic `_kill_tree` stays conservative. Both clients close the handle and
retain/report uncertainty if job accounting/termination cannot be confirmed,
including on subsequent close attempts. No filesystem/network sandbox is claimed.

Adopt executor manifest persistence/check-before-launch and job collection
tombstones. Runtime manifest identifies the configured interpreter/version or
Docker image contract, not environment provisioning or independent attestation.
Docker names alone are not immutable identities, and an unpinned image tag
remains mutable. Crash-time local job uncertainty remains conservative.

Adopt installed-wheel CI after correcting two blocking defects: its new checkout
pin was a malformed short SHA, and the shell word `dist/*.whl'[api]'` did not
match a normal wheel because the quoted extra remained part of the pathname
pattern. The corrected step finds the wheel first and quotes its path plus the
extra as one pip argument. All Actions pins now have 40 hex digits and the new
checkout pin matches the pre-existing jobs; this is a local structural check,
not a fresh remote Actions resolution.

Strengthen the smoke script: execute the distribution's actual `atomic` console
entry point under its venv scripts directory; require the API extra in CI via
`--require-api`; treat an ImportError inside an installed API as a failure rather
than silently skipping API coverage. Optional core-only invocations can still
skip when FastAPI itself is absent.

## Evidence and limits

Ruff passes for execution.py, windows_job.py, jobs.py, job_worker.py, MCP/LSP,
test_execution_profiles.py and wheel_smoke.py. Logs:
`validation/import-20261024/execution-ruff.txt` and
`execution-workflow-pins.txt`.

Initial ordinary pytest attempts were blocked by WinError 5 on pytest temporary
directories both in system Temp and under the workspace. They are infrastructure
failures, not test passes. The workspace attempt is retained in
`execution-tests-first.txt`. The focused execution/profiles/MCP run after MCP
ownership fixes passed **34 tests / 3 skips** (`execution-tests.txt`), including
the new accounting regressions. A separate LSP/profile follow-up is recorded in
`execution-lsp-tests.txt`: **38 passed / 1 skipped / 4 failed** in 732.11 seconds.
All four failures were LSP initialization deadlines, not job cleanup failures;
the separate four-case follow-up passed **4 / 4 in 17.32 seconds** without source
or deadline changes (`execution-lsp-recheck.txt`). This is consistent with
machine-load sensitivity; the failed original run remains evidence and the
combined LSP suite was not rerun a third time.
The coordinating agent owns final combined-tree wheel
build/smoke and overall integration evidence.

The external package-19 report explicitly used a Windows venv created with
`--system-site-packages`, then installed the wheel with `--no-deps`. Its PASS
proves an installed-package smoke on inherited dependencies, not a clean
dependency installation or isolated compatibility matrix. The new CI creates a
clean venv and resolves dependencies afresh, but it must actually run before
claiming Linux wheel acceptance. Freeze captures one run; broad dependency
lower bounds do not provide reproducibility. CI currently covers Ubuntu and
Python 3.11/3.12, not Windows/macOS or every version allowed by `>=3.11`.

The smoke exercises fixture availability, tree build/save/load, SDK import,
API health/readiness and CLI help. It does not demonstrate live model transport,
real editing quality, installed migrations, native isolation, load SLOs or
reproducible/signed releases. Docker was not installed or started and no paid
model calls were made in this review. The external checkout was never modified.
