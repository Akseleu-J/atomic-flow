# Package 23: external integrations and installed support matrix

The `integrations-release.yml` workflow runs installed wheel acceptance on
Ubuntu 24.04, Windows 2022 and macOS 14, Python 3.11/3.12, core and API extras.
Each clean external venv records the wheel hash, source commit, pip install
report (including downloaded artifact hashes), complete freeze and smoke output.
Only successful exact-source CI jobs constitute tested platform support.
`requires-python >=3.11` is an installation metadata floor, not evidence for
every future interpreter. Native filesystem/network sandbox support on Windows
or macOS is not asserted.

External acceptance starts real pinned `python-lsp-server==1.15.0` and official
`@modelcontextprotocol/server-filesystem@2.0.0` processes. The latter requires
Node >=24; runner compatibility is checked by the package engine requirement.
No npx download-on-execute, postinstall scripts, credentials, Docker or paid
model calls are involved. Primary tool versions are exact; Python freeze,
npm lock/integrity and dependency tree are retained. Transitives are resolved
on first installation: this is provenance of that run, not yet a committed
hash-complete cross-platform lock or bit-identical build promise.

`tools/integration_acceptance.py` runs TaskRunner plan review/approval, a staged
capability factory, actual LSP definition/full document synchronization before
and after a verified standard `patch_file`, MCP source reads of both revisions,
shared durable task spending and final host source publication. Original bytes
are checked unchanged during both staged probes. Model responses are scripted;
this proves protocol/runtime composition only, never live model edit quality.
The MCP tool grant binds the discovered read tool schema digest; only its exact
read operation is approved. Servers are trusted public development dependencies,
not untrusted plugins or operating-system isolation.

Pylsp announces incremental sync. The client now also accepts sync kind 2 while
continuing to send complete versioned text. The official LSP 3.17
[ContentChangeEvent definition](https://raw.githubusercontent.com/microsoft/language-server-protocol/gh-pages/_specifications/lsp/3.17/textDocument/didChange.md)
defines text-only events as whole-document replacement; no guessed ranges or
weakened read/version/hash gates are introduced. Upstream
[pylsp implementation](https://github.com/python-lsp/python-lsp-server/blob/v1.15.0/pylsp/python_lsp.py)
and [MCP release](https://github.com/modelcontextprotocol/servers/releases/tag/v2.0.0)
provide independent provenance.

Release gates still require the workflow's actual terminal results and retained
artifacts. Native sandboxing, independently evaluated live-model task quality,
signed release/SBOM publication and a transitive hash-complete dependency lock
are distinct gates. Local installation/execution evidence will be recorded
under `validation/step23-integrations/`; a workflow definition alone is not PASS.

## Measured local interoperability

On Windows/Python 3.12, actual pylsp 1.15.0 and official filesystem MCP 2.0.0
(Node 24.22.1, pnpm install with scripts disabled) completed the staged journey.
Both real definition lookups resolved `sample.py`, synchronized document version
increased 1 -> 2, both MCP reads returned the staged revision, original bytes
stayed unchanged during probes, and the final reviewed patch was published.
Seven scripted calls settled through the shared task ledger; accounting values
are synthetic fixtures and not provider billing. Final syntax/approved-diff
checks passed. No tests or live model quality are inferred from that syntax gate.

The actual run exposed two previously untested composition defects: staged
workspaces under application state were refused by LSP's hardcoded ordinary
source admission, and Windows analyzer lower-case drive URIs were rejected as
noncanonical. LSPClient now accepts explicit host-only `private_workspace=True`
while default clients still refuse private state, and normalizes only the drive
letter case on Windows. Independent admission/URI regressions passed 2 tests;
the incremental negotiation regression passed 1 test. Other path/credential/
scope/hash/version gates remain intact. Prior failed runs are retained, including
a Windows CRLF fixture mismatch fixed by deterministic initial file bytes.

Local analyzer dependencies were installed into a task-local directory and
copied into a tool venv with inherited core runtime dependencies. This is real
external protocol evidence, not the clean wheel support matrix. The latter
requires the exact-source CI terminal artifacts. Public package primary versions
are pinned but transitive Python/npm dependencies remain first-resolution with
retained freeze/lock provenance, not a hash-complete committed lock.

## Extended W6 composition acceptance (24 October continuation)

The prior real task already covered a reviewed staged Python edit, syntax gate,
MCP/LSP revision reads, shared accounting and publication in one TaskRunner.
Describing all of W6 as having no end-to-end run was too broad. Its missing
composition evidence was the explicit planning/review API, a real installed
regression checker, notebook source editing and failure/recovery boundaries.

The acceptance script now exercises public `prepare -> review -> approve ->
resume` calls. Attempting resume before approval must leave the task paused,
without a changeset or another model call. One digest-reviewed task has two
dependent steps: Python edit with real LSP/MCP before/after probes, followed by
notebook read/preview/apply and the installed pytest checker. The existing
six-turn per-role protocol cap remains unchanged. The second step receives a
fresh host capability binding to the same staged workspace and the same durable
task budget. The host regression file is outside the two-file mutation scope.
Final checks require a real passing pytest result before publication. Notebook
IDs survive, outputs/execution counts/trust markers are cleared, saved outputs
never enter the scripted model messages, and notebook cells are not executed.

Two separate scenarios start real servers and perform a verified staged Python
edit: host cancellation after both read-only external probes must restore the
checkpoint and preserve original source; an injected external editor changes
the original and stale approval must abort without overwriting that version.
The host fault fixtures are trusted Python, not authority obtained from model
input. All received scripted responses must have completed budget events and
the durable ledger must equal task spending, including planning.

After successful publication a fresh TaskRunner verifies the exact durable
boundary where the changeset journal is published but the task's completion
save was lost. It must finish without any model call or capability recreation.
This is explicit durable-state fault injection using public task persistence,
not a claim that an OS process was killed at that instruction or that every
crash boundary is covered. It preserves the existing recovery contract.

The new command still consumes no provider quota and downloads nothing. Local
pytest is allowed only for these trusted synthetic fixtures; no native sandbox
or untrusted-code isolation is implied. The external CI job retains its exact
tested Git commit along with dependency provenance and the structured result.
`--scenario publish|cancel|source_conflict` supports focused rechecks; the
default `all` runs all three in separate temporary projects and host states.

Intermediate evidence remains in `validation/step23-integrations/`: the first
expanded fixture exceeded the unchanged six-turn cap (`composition-v2-*`), so
it was split into two reviewed steps. A subsequent run hit the existing
45-second analyzer initialize deadline on the laptop (`composition-v3-*`).
No deadline or safety gate was relaxed. The final run's status is recorded in
`composition-final-result.json`; only a passing result is acceptance evidence.

The final Windows/Python 3.12 run **passed all three actual external-service
scenarios** with the unchanged deadlines. Publish used 14 completed scripted
responses (one planner, two per-step parser/critic pairs and nine answerer
responses), settled 14 task budget events, ran pytest successfully and published
both approved files. Its fresh-host recovery sent zero model requests and
created zero capabilities. Cancellation used six completed responses and left
an aborted changeset with original/checkpoint bytes preserved. Source conflict
used eight completed responses and preserved the external editor's version;
its changeset was aborted. Synthetic accounting values were USD 0.014 / 0.006 /
0.008 respectively; these are fixture amounts, not provider charges.

The final report's script SHA-256 matches the actual script bytes. This closes
the demonstrated W6 journey and named failure boundaries on this local target;
the updated exact-source Linux CI run still needs its own terminal evidence.
It does not establish all capability combinations, multi-root atomicity,
operating-system crash/power-loss behavior, untrusted-code isolation or model
quality. Historical single-step results above are retained as earlier evidence.

`tests/test_composition_acceptance.py` separately exercises the same public task,
notebook, ledger, checker and publication branches with public LSP/MCP constructor
substitutes. These focused fixture checks do not count as real protocol evidence;
the real process results are the separate artifacts described above.
All three focused tests passed (101.58 seconds on this laptop); scoped Ruff and
whitespace validation passed. Logs are `composition-unit.txt` and
`composition-ruff.txt`. Local shell wrappers that subsequently print a log can
mask a child exit code, so status claims here use the structured assertions and
test summaries; the CI command directly propagates the Python script's status.
