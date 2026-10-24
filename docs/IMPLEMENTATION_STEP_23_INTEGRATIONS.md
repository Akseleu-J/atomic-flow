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
