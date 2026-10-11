# Quick start

## Install and connect Kaggle

Python 3.11 or newer is required. Install the API extra when you want to use the HTTP server:

```bash
pip install -e '.[api,kaggle,benchmarks]'
kaggle auth login
atomic kaggle init --yes
atomic kaggle status
```

Kaggle has two separate credentials. `kaggle auth login` signs the CLI into your Kaggle account. `atomic kaggle init --yes` asks the Kaggle CLI to create a short-lived Model Proxy inference credential, stores it in `~/.atomic_flow/kaggle.env`, and writes local model defaults and an example task under `~/.atomic_flow/`. Never commit the credential file or paste its key into project configuration. `atomic kaggle status` displays expiry and available models without printing the key. Refresh credentials with `atomic kaggle auth` when expired.

The default backend for this development setup is Kaggle. `LLM_DEFAULT` selects the model initialized by Kaggle; you can also pass `--model` or set `backends.kaggle.model` in configuration. `atomic kaggle status` prints the current local token allowlist; `atomic kaggle models` queries Kaggle's task model catalog. Kaggle's `KAGGLE_API_TOKEN` authenticates Kaggle API operations and does not replace `MODEL_PROXY_API_KEY` for inference.

OpenAI-compatible and Anthropic backends are also available. Set `ATOMIC_FLOW_API_KEY`, `ATOMIC_FLOW_BASE_URL`, and `ATOMIC_FLOW_MODEL`, or edit `~/.atomic_flow/config.yaml` to select another backend. For Ollama, a typical base URL is `http://localhost:11434/v1` and the API key can be any non-empty string.

## Index a project

From the project directory:

```bash
atomic init --project
atomic tree build
atomic tree explain src/
```

The index is stored in `.atomic_flow/tree.json`. It contains paths, symbol names, signatures, line ranges, imports, and call names. Source bodies are read from the project when context is assembled and are not copied into the index.

## Ask a question

```bash
atomic ask "What does Session.refresh do?" --mode medium --explain
atomic ask "Review the token refresh flow" --mode hard --yes
```

`simple` makes one Answerer call. `medium` parses the query, answers with a project skeleton and selected code, and asks a Critic to review citations. `hard` also lets the Refiner and Expander select nodes and records each attempt. Hard mode asks for confirmation when any role uses a built-in prompt. `--yes` acknowledges that confirmation.

When `auto_mode` is enabled, requests mentioning reviews, refactors, migrations, or tracing start in hard mode. An explanation request with `--file` starts in simple mode. Choose `--mode` to set a mode directly or `--no-auto-mode` to use the configured default.

Pass `--role-prompt answerer=@./prompts/answerer.md` to override a role for one request. `atomic ask ... --edit-prompts` opens role prompt files under `.atomic_flow/prompts/` for editing.

## Benchmarks

The checked-in local cases use small fixture projects and verify answer terms, citation correctness, token counts, provider-reported cost, and latency:

```bash
atomic benchmark list
atomic benchmark run --dry-run
atomic benchmark run --model <model-from-atomic-kaggle-models> --yes
atomic kaggle quota
```

The dry run does not call a model. A real local run can use inference quota and saves a JSON report in `~/.atomic_flow/benchmarks/runs/`. Kaggle-hosted tasks are separate files in `benchmarks/kaggle_tasks/`; each file defines and runs one task. `race_condition.py` ports the attached reference benchmark: it requests a Pydantic-structured fix, executes the code, checks for `800000`, limits the explanation to 30 words, and has Kaggle's judge assess the explanation. Publish it with:

```bash
atomic benchmark push atomic-flow-invoice-subtotal --wait
atomic benchmark kaggle-list
atomic benchmark run-kaggle atomic-flow-invoice-subtotal --model <available-model> --wait --yes
atomic benchmark status atomic-flow-invoice-subtotal
atomic benchmark log atomic-flow-invoice-subtotal --model <available-model>
atomic benchmark download atomic-flow-invoice-subtotal --model <available-model>
```

For the attached race-condition benchmark, substitute `fix-race-condition-in-cpython-threading` as the task name in `push`, `run-kaggle`, `status`, `log`, and `download`.

Remote `run-kaggle` schedules an actual Kaggle run and may use quota. The CLI requires an explicit model and `--yes` acknowledgment for that operation. Kaggle's benchmark task format, model availability, and proxy quota are managed by Kaggle; consult its [benchmark guide](https://github.com/Kaggle/kaggle-cli/blob/main/docs/benchmarks.md) and [task user guide](https://github.com/Kaggle/kaggle-benchmarks/blob/ci/user_guide.md).

## Start the API

```bash
atomic up --host 127.0.0.1 --port 8000
```

The server exposes `GET /health`, `POST /v1/answer`, and `POST /v1/chat/completions`. The chat endpoint accepts `atomic-simple`, `atomic-medium`, and `atomic-hard` model names. Streaming responses are not supported yet. `/v1/*` accepts `Authorization: Bearer <server-key>` or `X-API-Key: <server-key>` when `ATOMIC_FLOW_SERVER_API_KEY` is set. The project path is restricted to `ATOMIC_FLOW_PROJECT_ROOT` (the server's current directory by default). To listen beyond loopback, set both `ATOMIC_FLOW_SERVER_API_KEY` and `ATOMIC_FLOW_PROJECT_ROOT`; the server refuses an unsafe network bind without them. Clients should use a distinct server key, never the Kaggle inference key. `atomic up` is intended for local development; put it behind a TLS reverse proxy and use a managed secret store before exposing a service to a network.

## Configuration

User configuration lives in `~/.atomic_flow/config.yaml`; project overrides live in `.atomic_flow/config.yaml`. The project values override user values, followed by environment variables for the backend URL, key, and model.

```yaml
mode: medium
context_budget_tokens: 8000
max_attempts: 3
backends:
  openai-compatible:
    base_url: https://api.openai.com/v1
    api_key: ""
    model: gpt-4o-mini
  anthropic:
    base_url: https://api.anthropic.com/v1
    api_key: ""
    model: your-anthropic-model-id
default_backend: anthropic
```

Use `atomic doctor --check-backend` for a live connectivity check. It sends a small billable request to the configured model.
