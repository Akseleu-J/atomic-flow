# Architecture (0.2)

1. **tree.builder** - AST index of files, classes, functions (full signatures, line ranges, docstring summary).
   Node id = `path::Qualified.symbol` (`#2` suffix for property setters/overloads). Raw call names are *resolved*
   into call-graph edges (same file -> imported files -> project-unique name); ambiguous names create no edge.
   File nodes carry `content_hash`/`size`/`mtime_ns`: `tree refresh` re-parses only changed files and `ask`
   auto-refreshes (`tree.auto_refresh`). Respects `.gitignore` and `tree.ignore`.
2. **activation** - `text.py` splits identifiers (`is_refundable` -> `is`, `refund`) and stems lightly;
   `ActivationIndex` is a per-tree inverted BM25F index (symbol x3, path x1.5, signature x1.5, summary, callees, imports),
   exact-name priors compare whole tokens, then call-graph spreading adds callees/callers/parent class of the best hits.
   `weak` is set when almost no query term occurs in the best nodes (heuristic, not a probability).
3. **materialize** - budgeted context: ranked skeleton (expanded for relevant files, one line otherwise), source slices with
   line numbers, parent/child de-duplication, per-node zoom that *degrades* to fit, stale-file refusal.
4. **orchestrator** - simple / medium / hard role flows. Nodes are shown to the model as short ids (`n12`); code goes into
   the prompt as raw text, not JSON-escaped; one JSON-repair round per call; citations are validated against the nodes that
   were actually in context; backticked identifiers absent from the context lower `grounding` and veto an "ok" verdict.
5. **backends** - Kaggle proxy, OpenAI-compatible, Anthropic; shared retrying HTTP helper; per-provider defaults;
   optional price table for cost/budget.
6. **api** - `/v1/answer`, `/v1/chat/completions` (retrieval uses the last user turn), `/v1/models`, constant-time key check,
   concurrency cap, project-root confinement.
7. **benchmarking** - `retrieval` (free), `run` (end-to-end, `--repeats`, regex checks, abstention case, grounding), Kaggle tasks.

| Mode | Flow |
|---|---|
| simple | activation -> Answerer (+ Parser only if retrieval is weak) |
| medium | Parser -> activation -> Answerer -> Critic -> one retry |
| hard | Parser -> Refiner -> Answerer -> Critic -> Expander/Reparser loop (reparse re-ranks; retry happens even if nothing new was found) |

`verdict` is a model opinion. `grounding` is deterministic but only checks backticked identifiers; neither is a proof of correctness.

## Not implemented
Streaming, TLS, per-user keys, rate limiting, tree-sitter parsing for non-Python languages, dense encoder (the optional
`atomic-core` encoder is a placeholder: implement `ActivationIndex`-compatible scoring and blend it with BM25 via rank fusion).
