# atomic_flow

Project-aware LLM development platform: indexes source code, selects relevant context, answers with citations,
**checks the answer deterministically against the context it was given**, and measures quality with free retrieval
metrics plus local and Kaggle-hosted benchmarks. Kaggle Model Proxy, OpenAI-compatible and Anthropic backends.

```bash
pip install -e '.[api,kaggle,benchmarks,dev]'
pytest                                   # no network, no keys needed (scripted fake backend)
atomic init --project && atomic tree build
atomic benchmark retrieval               # FREE: ranking quality, zero model calls
atomic ask "What does Session.refresh do?" --explain
```

Kaggle: `kaggle auth login`, `atomic kaggle init --yes`, `atomic kaggle status`, then
`atomic benchmark run --model <available-model> --repeats 3 --yes` (check `atomic kaggle quota` first).

## What changed in 0.2

See `REVIEW.md` (problems found in 0.1 and how each was fixed) and `docs/architecture.md`.
Tree format is v2: run `atomic tree build` once after upgrading.

## Safety defaults

* A cloned repo's `.atomic_flow/config.yaml` cannot set `backends`/`default_backend` until you run `atomic trust`.
* `atomic up` binds to loopback; network binds need `ATOMIC_FLOW_SERVER_API_KEY` (>=16 chars) and `ATOMIC_FLOW_PROJECT_ROOT`.
* Run artifacts (`~/.atomic_flow/runs/`) contain prompts and source snippets: `artifacts.save: false` disables them,
  `atomic runs prune` applies `artifacts.keep_days`.
* `budget.daily_limit_usd` is enforced locally (calls without a known cost are not counted; set `price_in_per_mtok` /
  `price_out_per_mtok` on a backend to make cost known).
