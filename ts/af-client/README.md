# Native single-owner TypeScript client

Zero runtime dependencies. Node 22+ or a browser with Fetch, streams and WebCrypto.
Build/test with `npm ci --ignore-scripts` then `npm test`. TypeScript is pinned to
5.8.3; tests use the Node test runner. The package contains built JavaScript/types
and this README, without fixtures or repository source.

```ts
import { AfClient, parseVerificationReport } from "@atomic-flow/af-client";
const client = new AfClient({ baseUrl: "http://127.0.0.1:8787", token });
await client.health();
const capabilities = await client.capabilities();
const session = await client.createSession(undefined, "existing-host-root-grant");
const snapshot = await client.readSession(session.session_id);
for await (const event of client.chat({
  backend: { provider_id: "scripted", model: "scripted" },
  session_id: session.session_id,
  root_id: "existing-host-root-grant",
  prompt: "Explain the selected source excerpts",
})) console.log(event.type);
```

The second `createSession` argument is a **root ID**, replacing the historical
project path. `jobStatus()` accepts no store path. `appendUserInput(id, payload)`
replaces arbitrary `appendEvent`: browser input cannot mint terminal, approval,
snapshot or reconciliation events. Provider configuration is host-owned:
`backend` accepts only `provider_id` and `model`, never URLs or environment names.
`runEditJourney` retains an exact explicit replacement/host approval request;
current success remains `verified:false`, `verification:not_assessed`. The Studio
safe apply UI remains unavailable until an admitted checker contract is wired.

Every response is checked at runtime. Duplicate JSON keys, unknown DTO fields,
bad digests, unsafe counters, invalid UTF-8 and oversized bodies/events are
rejected. `parseVerificationReport(raw)` is asynchronous to verify quote SHA256
with WebCrypto; it follows the Rust `af-evidence` frozen wire vectors. Reports
are **data only**. Even a valid `passed` or `published` label supplies no grants,
execution receipt, semantic truth or new authority.

Session listing (`listSessions()`), budget snapshots (`readBudget()`) and
structural graph reads (`readGraph(rootId)`) run through named wire validators
alongside `health()` and `readSession()`. Field-set violations raise `WireError`
(a subclass of `AfProtocolError`) naming the offending fields; value violations
stay `AfProtocolError`. Budget amounts are exact nanodollar strings from
af-budget — never floats — and `hold`/unknown charges stay explicit. Session
call identifiers are strings on the host journal wire. Graph reads carry
per-file layers and symbols: structural data only, never a read proof.

HTTP is permitted only on loopback; other endpoints require HTTPS. URL userinfo,
queries and fragments are refused and redirects use `error`. Responses are read
incrementally with byte limits, including missing/false Content-Length. A chat
stream has bounded total bytes/events, requires a terminal outcome, and aborts
when its iterator closes. `chat(input, {signal})` supports explicit cancellation.
There is no retry or reconnect replay. `AfTransportError.outcome` is `unknown`
and `retryAllowed` is false: inspect the host journal/budget before continuing.

Tests against mocked transport and frozen Rust DTO vectors are contract evidence.
They do not establish live provider quality, isolated execution, native full
TaskRunner parity, corporate ACL or production readiness. An installed-copy
consumer against the actual native server is a separate integration check.
