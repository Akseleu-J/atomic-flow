# Studio HTTP boundary, 4 November 2026

Changed scope: `rust/af-cli/src/server.rs` and `http_safety.rs`. The parent
integrates the separate trusted-provider, inspection and runtime modules.
No provider request, native folder dialog, commit or push was performed by
this subtask. Rustfmt and diff whitespace checks passed. Compilation and
test execution belong to the parent's serialized build; no unrun test is
reported as a passing test here.

## Implemented boundary

- Exact listener port and literal loopback Host admission without a token;
  authenticated remote IP access still requires an exact Origin/Host match.
  Foreign/null Origin, cross-site fetch metadata, duplicate Host/Origin,
  inconsistent absolute URI authority and duplicate bearer headers refuse.
- Static dashboard remains available for memory-only bearer entry; every
  data/effect endpoint keeps server authentication. HTML receives a fresh
  script nonce and CSP without inline script execution. Security response
  headers disable sniffing, framing, referrer and response caching.
- Body, prompt and editing-input bounds; at most four stream workers, one
  editing worker and one native picker. Blocking model/edit work runs outside
  async request handling. SSE uses one bounded 32-item queue, 8 KiB chunks,
  256 KiB total delta and 128 KiB terminal content ceilings. Dropping the HTTP
  stream cancels its actual backend token. No automatic replay is introduced.
- Provider selection uses the parent's host configuration and durable budget
  wrapper. Browser endpoint/environment-variable authority is refused. Paid
  calls require a durable session. Journal checkpoints precede backend work;
  complete usage/content is recorded before final delivery or cancellation.
  Unknown outcomes retain the hold. HostAttempt carries the same session/call
  identity into the budget wrapper; browser JSON cannot set this host field.
- Session project binding comes from a current granted root. Browser project
  filesystem paths are rejected. Context excerpts come from the parent's
  bounded, redacted, current-hash source reader and remain not assessed.
- Browser event append permits only advisory user input, never fake model/tool
  completion, host reconciliation, snapshot or verification events. Graph reads
  revalidate the root grant/identifier. Job reads are confined to the fixed
  host job store. Root registration supports an explicit exact write-path list;
  default registration stays read-only. Editing does not widen it silently.
- Editing binds the reviewed target path and replacement bytes, retains
  `Unverified` separately and never derives verification from process exit zero.
- Budget projection distinguishes known spent, reserved and unknown exposure.
  `committed_usd` explicitly means known spend plus reservations. HOLD returns
  a null available balance, not a measured zero. Billing caps are not promised.

## Regression cases supplied for parent execution

Host/Origin/port admission and authenticated remote Origin; forged browser
terminal events; provider destination/credential-variable refusal; graph/job
path confinement; request-size limits; public nonce-protected authentication
UI versus private APIs; actual dropped-stream worker cancellation; oversized
terminal refusal; durable settled chat; unchanged unknown-call journal and
checkpoint after attempted continuation; spent/reserved/HOLD API projection.

Existing tests that mutate `ATOMIC_FLOW_HOME` require the repository's
serialized `--test-threads=1` execution. Real provider billing, Linux Docker
isolation and complete production/tenant isolation are separate acceptance
scopes. This API is a trusted local operator application, not a multi-tenant
service or a TLS reverse-proxy profile.
