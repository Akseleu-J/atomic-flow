# Quick start

## Install and connect Kaggle

Python 3.11+.

```bash
pip install -e '.[api,kaggle,benchmarks,dev]'
kaggle auth login
atomic kaggle init --yes
atomic kaggle status
```

`kaggle auth login` signs the CLI into your account; `atomic kaggle init --yes` creates a short-lived **Model Proxy** key in
`~/.atomic_flow/kaggle.env` (created 0600 *before* the key is written). Never commit it. `atomic kaggle auth` refreshes it.
OpenAI-compatible (`ATOMIC_FLOW_API_KEY`, `ATOMIC_FLOW_BASE_URL`, `ATOMIC_FLOW_MODEL`) and Anthropic (`ANTHROPIC_API_KEY`) backends also work.

## Index and ask

```bash
atomic init --project
atomic tree build            # `tree refresh` is incremental; `ask` refreshes automatically
atomic tree explain src/
atomic ask "What does Session.refresh do?" --mode medium --explain
atomic ask "Review the token refresh flow" --mode hard --yes
atomic ask "..." --dry-run --explain      # shows exactly what would be sent, no model call
```

`simple`: one Answerer call (plus a Parser call only if lexical retrieval finds nothing, e.g. non-English questions).
`medium`: Parser -> Answerer -> Critic -> one retry. `hard`: adds Refiner/Expander/Reparser. Output shows the critic's
`verdict` (model opinion) and `grounding` (deterministic check of backticked identifiers against the supplied context).

`--edit-prompts` opens role prompts under `.atomic_flow/prompts/` and exits; rerun the query afterwards.

## Benchmarks

```bash
atomic benchmark retrieval                 # free: hit@k / MRR of the ranking, no model calls
atomic benchmark run --dry-run
atomic benchmark run --model <model> --repeats 3 --yes
atomic kaggle quota
atomic benchmark push atomic-flow-invoice-subtotal --wait
atomic benchmark run-kaggle atomic-flow-invoice-subtotal --model <model> --wait --yes
```

Fixtures live only in `src/atomic_flow/benchmark_data/` (single source of truth). Cases declare `expected_nodes`, regex `must_match`
(word-bounded, so "percent" does not satisfy "cent"), and `must_abstain` for unanswerable questions.
Kaggle task files in `benchmarks/kaggle_tasks/` test the *model* on inline snippets; `race_condition.py` is the attached reference benchmark.

## API

```bash
export ATOMIC_FLOW_SERVER_API_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
atomic up --host 127.0.0.1 --port 8000
```

`GET /health`, `GET /v1/models`, `POST /v1/answer`, `POST /v1/chat/completions` (`atomic-simple|medium|hard`; streaming unsupported).
Keys: `Authorization: Bearer` or `X-API-Key`. Project paths are confined to `ATOMIC_FLOW_PROJECT_ROOT`.

## Configuration

`~/.atomic_flow/config.yaml`, then `.atomic_flow/config.yaml` (backend settings only after `atomic trust`), then environment.

```yaml
mode: medium
context_budget_tokens: 8000
default_backend: anthropic
backends:
  anthropic:                     # base_url/api_key_env default to Anthropic's
    model: your-model-id
    price_in_per_mtok: 3.0       # optional: enables cost + daily budget enforcement
    price_out_per_mtok: 15.0
  ollama:
    type: openai-compatible
    base_url: http://localhost:11434/v1
    model: qwen2.5-coder
budget: { daily_limit_usd: 10 }
artifacts: { save: true, keep_days: 30 }
tree: { respect_gitignore: true, auto_refresh: true }
```

`atomic config get [key]` shows the merged config with secrets masked; `atomic config set` validates before writing.
