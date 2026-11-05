# Imported routing review — 2026-10-24

Reviewed source: external `c8820ff`, read only. Adopt the provider usage, quota,
and routing work after the integration corrections below. This is an offline
code and fault-matrix review, not live provider acceptance.

## Adopted

- Numeric OpenRouter cost, including explicit zero; monetary usage is normalized
  before generated content is parsed. `ResponseShapeError` preserves received
  usage so accounting callers settle charges before propagating malformed output.
- Host-only named routes, declared tariffs and expiring free-tariff confirmation;
  project YAML cannot supply routes or redirect credentials.
- Confirmed HTTP 429 refusal is the only failover-triggering transport error.
  Unknown submission, completed malformed output and 401/403 do not fail over.
- Durable local cooperating-process quota admission with unknown-attempt holds,
  explicit host reconciliation, persistent Retry-After and per-attempt identities.

## Corrected before adoption

1. `free_only` previously accepted a confirmed-free configured route even when
   its response reported a positive actual charge. The imported test's successful
   fake response itself cost USD 0.001. It now requires a zero-cost result after
   the accounting callback; a nonzero/unknown result raises `RoutePolicyError`
   carrying `usage_result`, without failover. Existing incurred charges remain
   recorded. This detects a tariff contradiction after a response; configured
   prices cannot prevent a provider from charging contrary to its configuration.
2. Quota admission preceded accounting admission. Host budget/preflight failures
   lacked transport outcomes and stranded pending quota slots despite no send.
   Accounting owners now annotate pre-send failures explicitly; routing releases
   unsent slots and returns their local request/token window capacity. Refused
   requests still consume local quota. Unknown submissions retain their holds.
3. Remaining time was computed before a potentially blocking quota lock. Routing
   now uses bounded cancellable nonblocking lock acquisition, checks the deadline
   and tariff expiry again after reservation, and carries an absolute deadline
   through the selected backend so accounting preflight cannot extend it.
   OpenAI-compatible and Anthropic transports enforce that absolute send deadline.
4. OpenAI optional-parameter negotiation previously allowed multiple physical
   sends behind one route quota reservation. Routed OpenAI calls now stop on that
   refusal; standalone negotiation shares one overall deadline.
5. Falsy malformed usage (`false`, `0`, `[]`, empty string, null) previously became
   an empty usage object and could be classified as known zero under a configured
   free tariff. Any explicitly present non-object usage is now invalid, preserving
   uncertainty rather than manufacturing a zero.
6. Quota state now bounds file size and validates object/version, integer window
   counters and pending metadata. A quota-completion persistence failure preserves
   received usage or the original transport outcome and never authorizes failover.

## Integration contract

The accounting owner distinguishes pre-send, in-transport unknown, and fully
received states. Post-response exceptions retain their `ChatResult` where
available, including cost-limit and cancellation errors. Every physical attempt
settles independently against the same parent task and daily ledger. Route policy
errors are completed response errors, never zero-cost preflight failures.

## Limits retained

Quota windows are local UTC fixed windows, not authoritative provider quotas or
distributed service limits. Price estimates are admission estimates, not billing
guarantees. No account/key rotation or automatic creation of new quota identities
is implemented. No live Groq/Gemini/OpenRouter compatibility or free-tier
availability was tested. HTTP/API/index changes are primarily covered by the
separate import reviewers; this review's implementation ownership is routing and
OpenAI usage/transport contracts.
Native tool transcript migration remains unsupported by routing v1. Anthropic's
existing content/usage parser and cache-price estimate have not received the
OpenAI normalization rewrite; its live usage contracts remain a separate gate.

Validation: focused offline tests and lint; logs are under
`validation/import-20261024/`. Paid/network model calls: zero. No full suite or
Docker invocation performed for this review.

Final combined provider/routing/accounting check: **67 passed** on Windows,
using `test_routing.py`, `test_routing_import_review.py`,
`test_provider_contracts.py`, and the parent-owned `test_routed_accounting.py`.
Log: `validation/import-20261024/routing-accounting-focused.txt`. Scoped Ruff
check passed. The initial sandboxed run hit filesystem ACL errors in pytest
temporary directories; the passing offline run used the approved elevated test
invocation, without changing assertions or disabling protections.
