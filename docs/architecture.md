# Architecture

The current development release has these pieces:

1. `tree.builder` walks a project and uses Python's AST parser to record files, modules, classes, functions, signatures, imports, and call names. Other common source and config files appear as file nodes and can contribute a bounded opening excerpt to context. Ignored directories and files larger than 1 MB are skipped.
2. `activation.activate` ranks nodes with BM25-style lexical matching. This is the no-download fallback described in the design. The optional atomic-core encoder is not wired in yet.
3. `activation.materialize` renders a compact skeleton and reads selected source ranges from disk, bounded by an approximate token budget.
4. `llm.orchestrator` runs the role protocol. Pydantic validates every role response. Hard-mode attempts and prompt inputs are saved beneath `~/.atomic_flow/runs/`.
5. The backend router supports Kaggle Benchmarks Model Proxy, OpenAI-compatible chat completion endpoints (including local servers such as Ollama and vLLM), and the native Anthropic Messages API. Kaggle Model Proxy credentials are stored separately from project configuration, masked in status output, and checked for expiration and model access before inference.
6. The API exposes native answer and OpenAI-compatible routes. It supports bearer or `X-API-Key` authentication, limits project access to a configured root, bounds request sizes, and refuses a non-loopback CLI bind unless an API key and project root are configured.
7. Local benchmark cases measure answer terms and citation correctness, and save JSON reports with provider-reported token usage, cost, and latency. Kaggle task scripts can be pushed, run, inspected, and downloaded through the Kaggle CLI.

## Tree format

Node IDs have the form `sha1:<digest>:<path>::<symbol>`. They remain stable while a path and qualified symbol stay the same. The tree has a version field and can be rebuilt with `atomic tree refresh`. Incremental updates and Git churn metrics are planned but are not part of this release.

## Roles

| Mode | Flow |
|---|---|
| simple | activation → Answerer |
| medium | Parser → activation → Answerer → Critic → one retry when needed |
| hard | Parser → Refiner → Answerer → Critic → Expander/Reparser loop |

Each role is asked to return JSON and the response is parsed against its Pydantic model. Citation IDs are filtered against the nodes included in that attempt's context. Critic verdicts are model judgments; this release does not provide an independent formal proof checker.

## Prompts and artifacts

Prompt lookup order is request, project (`.atomic_flow/prompts/`), user (`~/.atomic_flow/prompts/`), then built-in. Every run saves its request, role prompts and outputs, context, ledger, final answer, and metadata. Project trees contain structure and signatures, not full source bodies.

## Current scope

## Operational limits

This is a development platform, not a multi-tenant hosted service. The API key is a single shared server key; key issuance, per-user scopes, rotation, rate limiting, and server-side daily spend enforcement are not implemented. The server has no built-in TLS or streaming support. Kaggle controls the proxy model catalog and quota, and provider cost is only reported when the response includes it. The optional atomic-core encoder, SQLite, team reports, and automatic Git refresh hooks are not implemented.
