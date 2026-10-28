# P07 bounded model data queue — 28 October 2026

The Linux full TaskRunner and Docker executor can request inference through a dedicated GitHub scratch data branch. Linux receives only its automatic, narrowly scoped GitHub token. The Windows broker retains the physical ModelProxy credentials in its process. No Kaggle/provider credential belongs in a request, response, artifact, config file, Git commit or Linux environment.

This package supplies transport and broker contracts, with offline scripted tests. It does not establish a deployed broker, completed paid attempt, accepted model quality, or completion of P07. The host must supply durable admission/accounting and exclusive lock adapters before live use. This work made no physical provider call.

## Files and integration

- src/atomic_flow/backends/github_queue.py: injected TaskRunner-compatible text-JSON backend, local durable attempt journal, observe-only reconciliation, bounded GitHub Contents transport.
- tools/p07_model_broker.mjs: host-only ES module without credential loading, CLI side effects or automatic network activity. Import processOne with injected queue/proxy/journal/admit/settle clients.
- tests/test_github_queue_backend.py and tests/p07_model_broker_test.mjs: scripted failure, crash, replay, admission and canonical-byte checks.

Construct BackendConfig with model anthropic/claude-sonnet-5@default, text_json protocol, zero retries, explicit input/output limits and no provider credential/base URL fields. Inject GitHubQueueBackend(config, transport, identity, journal_root) into the trusted TaskRunner host. Transport must expose binding={repo, branch, prefix}; an injected transport may instead receive explicit transport_identity with those exact keys. The real Contents transport accepts scratch branch names beginning codex/p07-model-queue or p07-model-queue; create and review that data-only branch separately. Never execute its contents.

The backend name and copied config name include a SHA256 digest of the complete identity and queue binding. Existing framework config/name digests therefore change when owner, arm, task, campaign, runtime, workflow, profile or destination queue changes. Mutation of approved bindings/config fails before another request. These are integrity bindings for trusted hosts, not attestation of GitHub or authorized writers.

## Wire contract

Requests are at most 1 MiB of UTF-8 canonical JSON, with unpredictable mq_ plus 32 hexadecimal character call IDs. Exact identity fields are campaign_id, owner, arm, task_id, framework_sha, workflow_sha and profile_sha256. Only akseley and umirbayulgaisha are protocol owners; this does not itself grant account authorization. Runtime/workflow are full 40-character SHAs and profile is a full SHA256. Host admission must compare every identity field and the complete config with the exact reviewed profile and named owner authorization.

Config permits only the fixed model, bounded tokens, decimal-string temperature (or null), and zero retries. At most 256 messages permit only role/content text with system/user/assistant roles. No executable object or tool callback crosses this boundary. Prompts must be reviewed public benchmark input; repository text cannot grant cap or credential authority.

Canonical JSON sorts ASCII keys lexically, including numeric-looking keys, preserves UTF-8 text, uses compact separators, rejects lone surrogates and accepts only safe integer numbers. Cost and temperature use decimal strings. Both language tests pin the same Unicode/numeric-key byte vector and SHA256. Requests bind messages_sha256 and request_sha256; responses bind original call ID, request hash, identity hash and packet hash. Existing exact requests are accepted; conflicting requests are refused. GET/PUT are single attempts, and HTTP uncertainty cannot trigger another model send.

## Host safety and accounting requirements

Injected journal.withLock(fn) must hold a durable exclusive host single-writer lock throughout reservation, physical call, settlement and response publication. An in-memory Promise lock is suitable only for scripted tests. Journal reads/writes must be durable and keyed by original call ID. They cannot silently replace or clear unresolved calls. The queue adapter must create response files immutably or compare existing packets byte-for-byte. Provider/proxy exception strings and invalid raw responses are never journaled by this module.

admit(req) must enforce exact campaign/profile/owner/model/runtime/workflow/arm binding, operation permission, combined and owner caps, and prior unresolved charges. It durably reserves before returning {status:'admitted', reservation_id:...}. Known pre-send refusal returns {status:'refused'}. Exceptions or malformed admission results remain unknown, never proof of zero cost. The broker journals admitting before admission, preserving a crash during reservation. Campaign-wide unresolved state must block different call IDs too; this per-call broker does not replace that shared ledger gate.

After reservation the broker durably claims the original call, invokes proxy.complete(req,reservation) once with provider retries disabled, normalizes selected content/usage fields, persists received usage, and invokes settle(reservation,result,req). Only {status:'settled'} permits completion. Settlement retains original owner/call/day identity and actual reported cost. Missing/malformed cost is unknown, never zero. No callback may change account, model, profile or backend on failure.

A fully received unusable response may throw an exception with usage_result; normalized usage still settles and completion carries response_shape. The Python backend raises ResponseShapeError with usage_result for ordinary framework accounting. Physical or settlement failure produces unknown, preserves validated received usage locally and never resends. Invalid raw response objects are discarded.

Repeating completed/refused calls republishes the saved packet. Repeating admitting/claimed/received/unknown emits bound unknown without admission or send. Failure after durable completion but before publication permits exact packet publication later, without inference. Published unknown packets remain immutable. Operator reconciliation preserves the original attempt and ledger evidence.

Linux persists intent before queue submission. Poll timeout, post-submit cancellation, malformed or mismatched responses and transport failures latch unknown and block future sends from that journal. reconcile(call_id) only observes the original response; the host separately reconciles the original framework reservation. It does not create a new queue request or clear shared accounting holds.

## Focused validation to run

Run python -m pytest -q tests/test_github_queue_backend.py with project dependencies, and node --test tests/p07_model_broker_test.mjs. Tests do not contact GitHub/provider, load a vault, execute benchmark source or require credentials. They cover hash parity, approval binding, bounds, settled usage, refusal, unknown outcomes, crash-before-send, concurrent duplicate delivery, publication recovery and immutable conflict. Acceptance requires recording actual results; no passing result is claimed here.

Root verification: standalone Python queue/pytest-self contracts passed67 tests;
updated queue/self integration neighbors passed34; JavaScript broker passed11
tests. These overlapping sets are not additive. Independent review found a local
admission race; the backend now holds a shared OS lease across check/intent/wait.
It also observes the framework context-local cancellation scope. Pre-enqueue
cancellation is marked not_sent; after enqueue it preserves unknown and cannot
guarantee remote cancellation. Added concurrency/cancellation regressions.
A real GitHub queue smoke with a sealed scripted zero-provider host broker is
prepared; no paid transport or model-quality acceptance is claimed.

Updated root queue/self/smoke/label neighbors54 passed, Ruff clean; JS broker11
passed. New Python lease rejects concurrent chat before either can race intent.
Framework cancellation is observed through context-local scope; a new attempt
is blocked after cancellation once queued. A real scripted smoke is pending.
Temporary Model Proxy grant was obtained through the official token API without
inference; token and URL remain process-only. Catalogue ListBenchmarkModels
confirmed Sonnet5 default version152/modelProxySlug; this does not prove a live
inference route. GET model catalogue/proxy path probes returned404 and are not
used as a route success claim. Official CLI/local docs consulted:
https://github.com/Kaggle/kaggle-cli/blob/main/docs/benchmarks.md and
https://github.com/Kaggle/kaggle-benchmark-harbor-starter-template . No .env was
written and no provider credential was transferred to GitHub.

Actual zero-provider GitHub transport smoke accepted: CI40668983526 on9538c147271c6816dee736d5a9bec64c3a7ea48c, archive12807267815 SHA2561dc6b5defa46ca3441218c47ee949cbd164fb45c0a13ce65deaa2dbcd00b87dc. Request/response canonical digests and exact identity match durable completed journal. This is one scripted callback, not live Model Proxy inference or full TaskRunner. Evidence: validation/p07-budget20/model-queue-scripted-v1/acceptance.json. New full TaskRunner queued workflow and strict scripted proxy prepared; fresh remote qualification pending.
