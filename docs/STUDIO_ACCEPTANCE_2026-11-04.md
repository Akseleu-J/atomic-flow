# Atomic Flow Studio — working native surface and safety boundaries

The 4 November work continues the updated zcode checkout from `09d06c5`.
It preserves the Rust/TypeScript reconstruction and improves the existing
native Studio surface. This is a functional engineering increment, not full
production acceptance or a claim that every Python capability has migrated.

## Run

From this checkout's `rust/` directory:

```powershell
cargo run -p af-cli --locked --offline -- serve --bind 127.0.0.1:8787
```

Then open `http://127.0.0.1:8787/dashboard`. The built Windows executable is
`rust/target/debug/af.exe`. The default local test provider is deterministic;
it checks the connection and journal, and does not generate a model answer.
Existing project roots and historical paid holds are never imported/reset by
this increment. Use an isolated `ATOMIC_FLOW_HOME` for disposable testing.

For authenticated access, inject a process-only `AF_STUDIO_TOKEN` and run
`af serve --token-env AF_STUDIO_TOKEN`. Enter that existing token into Studio's
authorization dialog; it is retained only in the current browser tab's memory.
No credential is placed in a URL, saved to localStorage or sent in provider
configuration responses. Non-loopback binding still requires a server token;
plain HTTP remote deployment is not a supported secure deployment recipe.

## What now works

- Three zones with responsive panels, persisted panel widths, keyboard command
  palette, accessible controls, readable empty/error states and no external fonts.
- Explicit folder selection, project registration/revocation, structural graph
  building, redacted source preview with SHA256 and stale-source warnings.
- Bounded chat streams with stop/disconnect cancellation and mandatory terminal
  outcome; session/root switching invalidates stale UI state and evidence.
- Real journal history with bounded cursor pages and immutable journal snapshots.
  A journal snapshot is inspection data, not a backup of project source or replay.
- Source context is discovered in the graph, re-read from the current root grant,
  hash-checked and bounded before being sent to a provider. The source list means
  “context supplied,” not “every answer claim was independently verified.”
- Host-selected providers and durable per-attempt budget admission. Journal and
  ledger use the same host session/call identity. Missing/unknown actual cost or
  failed settlement retains HOLD; there is no automatic replay or hold reset.
- Browser-controlled URLs, credential environment names, hostile origins/Host
  values, forged model terminal events and arbitrary job-store paths are refused.

The native editing entry point binds exact reviewed paths and replacement bytes.
Generic exit-zero commands and no-edit answers remain `not_assessed`; they cannot
grant Verified or automatic Git publication. The UI keeps unsupported task/apply
workflows disabled until an admitted requirement checker and review/publication
contract are wired. This limitation is deliberate and visible.

Publication now persists exact before-byte backups and an immutable intent before
any source write, fsyncs the records, verifies replacements/rollback and retains
a durable root hold after an uncertain mutation. A fresh session or another ID for
the same root cannot clear that hold. This is cooperating-writer protection on a
trusted host, not protection against an adversary modifying the host filesystem.

## Configure a real provider on the host

`ATOMIC_FLOW_HOME/providers.json` (or the explicit process path
`AF_STUDIO_PROVIDER_CONFIG`) is a host-owned file, never a browser-write endpoint.
It contains configuration and the name of a process variable, not the API key:

```json
{
  "schema_version": 1,
  "providers": [{
    "id": "primary",
    "label": "My provider",
    "base_url": "https://provider.example/v1",
    "api_key_env": "ATOMIC_FLOW_API_KEY",
    "models": ["approved-model-id"],
    "reservation_usd": "0.05"
  }]
}
```

Choose the provider/model in Settings and create a durable budget before sending.
The reservation is an admission estimate, not a guaranteed provider billing cap.
Only finite nonnegative provider-reported `usage.cost`/`usage.cost_usd` settles
actual cost. A provider that omits cost leaves a HOLD after its response, even if
token usage is present. No tariff is invented and no unknown cost becomes zero.
Provider redirects are disabled; response/stream bytes and network waits are bounded.

## Validation and remaining gates

Local Windows targeted suites: af-agent 13, af-cli 22, af-llm 15, af-loop 37,
af-sandbox 17: **104 PASS**. The sandbox fixture initially omitted its required
lease; its failed run is retained and the corrected final sandbox run is separate.
The earlier HTTP fixture incorrectly expected scripted echo to repeat the prompt;
it now checks the actual scripted response. UI protocol/security tests: **8 PASS**.

The actual built native binary passed five end-to-end HTTP scenarios in isolated
state: authentication/CSP, hostile request refusal, Rust graph/source preview,
durable scripted chat/history/context, journal snapshot/hash and budget projection.
Owned server cleanup was confirmed. Evidence is in
`validation/zcode-studio-20261104/`; provider_calls=0 throughout.

Final native build completed without compiler warnings. A separate strict
Windows Clippy attempt stopped in unchanged `af-entropy` code on two redundant
Win32 pointer casts and the `OVERLAPPED` FFI type name; Clippy is not marked
passed. The first PowerShell-hosted build was interrupted by a shell stack
overflow; the retained non-login-shell continuation completed and was tested.

Browser visual QA is **not completed**: the in-app browser is disabled and Windows
Computer Use stopped because it could not determine Chrome's current URL safely.
No browser protection was bypassed. Full native Linux Docker acceptance, full
capability cutover, independent answer grading and real-provider quality are
separate gates. Historical research pairs/holdout/ablations remain unfinished.
