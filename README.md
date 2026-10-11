# atomic_flow

Project-aware LLM development platform: indexes source code, selects relevant context, answers with verifiable citations, and measures quality with local and Kaggle-hosted benchmarks. The current first-class hosted provider is Kaggle Benchmarks Model Proxy; OpenAI-compatible and Anthropic backends remain available.

## Kaggle development setup

```bash
pip install -e '.[api,kaggle,benchmarks]'
kaggle auth login
atomic kaggle init --yes
atomic kaggle status
atomic init --project
atomic tree build
atomic benchmark list
atomic benchmark run --dry-run
```

`kaggle auth login` authenticates the Kaggle CLI account. `atomic kaggle init --yes` obtains a separate, short-lived Model Proxy inference key and saves it in the user profile (`~/.atomic_flow/kaggle.env`). Keep this file private and out of version control. Set up a project, then run a local benchmark with `atomic benchmark run --model <available-model> --yes`; check `atomic kaggle quota` before spending inference quota. See [Kaggle setup and benchmarks](docs/quickstart.md).

## Local API

`atomic up` serves only on loopback by default. Configure `ATOMIC_FLOW_SERVER_API_KEY` to require a bearer key on `/v1/*`. Binding to a network interface also requires both this key and `ATOMIC_FLOW_PROJECT_ROOT`, which restricts project access to one directory. Keep provider credentials on the server; clients send the server key, never a Kaggle Model Proxy key.

Project state is saved under `.atomic_flow/`; user-wide configuration and run artifacts live under `~/.atomic_flow/`. See [docs/architecture.md](docs/architecture.md) for details.
