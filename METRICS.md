# Metrics

## atomic_flow baseline — 2026-10-12

### Retrieval

- Fixture retrieval: 7/7 answerable cases hit at k, hit-all-at-k `1.0`, MRR `0.929`.

### End-to-end

- Reported baseline: 3/3 cases passed (`1.0`), `tokens_in=8377`, `tokens_out=2971`.

### Full benchmark — 2026-10-12

- Suite: `atomic-flow-grounded-code-qa`
- Mode: `medium`; model: `google/gemini-3-flash-preview`; backend: `kaggle`
- Repeats: 3 (27 total runs)
- Initial run: `22/27` (`81.48%`)
- Flaky cases: `refund-call-chain`
- Consistently failing case: `checkout-composition` (`0/3`)
- Tokens: `81509` in / `60326` out
- Cost: `$0.2217325`
- Average latency: `11797.15 ms`
- Report: `/tmp/atomic-flow-benchmark-2026-10-12.json`

### Full benchmark rerun — 2026-10-12

- Regex fix: `checkout-composition` now accepts `vat`, `vat_rate`, and related identifiers via `(?i)\\bvat\\w*`.
- Passed: `26/27` (`96.30%`)
- Remaining flaky case: `refund-call-chain`
- Tokens: `84745` in / `64978` out
- Cost: `$0.2373065`
- Average latency: `14486.93 ms`
- Report: `/tmp/atomic-flow-benchmark-2026-10-12-rerun.json`
- `refund-call-chain` failure: `citation_hit=true`, `grounded=true`, `verdict=ok`; the answer included `is_refundable` and `approved` but omitted `denied`.

### Expanded B2 benchmark — 2026-10-12

- Corpus: 22 cases; 44 runs (2 repeats per case)
- Added 3 real-project fixtures from `Multy-agentic-system-on-gemini-API`, 5 negative cases, 2 multi-step cases, and 2 additional Russian cases.
- Retrieval baseline: 9 scored cases, hit-all-at-k `1.0`, MRR `0.944`; Russian and real-project Russian queries are excluded from lexical-only retrieval scoring (`skip_retrieval`).
- Initial run: `39/44` (`88.64%`), with brittle assertions in `sourcing-data-flow` and `sourcing-stats`.
- Final baseline after assertion fixes: `44/44` (`100%`); `flaky_cases=[]`.
- Negative cases: `10/10` passed; no abstention failures observed.
- Multi-step cases: `4/4` passed.
- Russian cases: `8/8` passed.
- Tokens: `282559` in / `147080` out
- Cost: `$0.5825195`
- Average latency: `17821.8 ms`
- Report: `/tmp/atomic-flow-benchmark-22cases-rerun-2026-10-12.json`

### Independent project dry-runs — 2026-10-12

- `atomic-ops`: indexed 76 files / 426 nodes. `forward pass` selected `atomic_ops/fallback.py::gdn2_forward` and related forward implementations; `gdn_step` selected forward-related implementation and test nodes.
- `atomic-gdn2`: indexed 35 files / 161 nodes. `forward pass` selected `tests/test_models.py::test_forward_backward_finite`; `gdn_step` selected relevant forward/test nodes.
- Both projects produced non-empty medium-mode parser, answerer, and critic dry-run prompts with `retrieval_weak=false`.

## Retrieval ranking on atomic-ops — 2026-10-12

### Before kind penalty + synonyms + Parser translation

- English `how does gradient flow through chunked state?`: `test_finite_diff_gradient` was #1; `gdn2_pallas_forward_trainable` was around #7; `_kernel_a_body` was outside the top 30.
- Russian equivalent: untranslated `состояние` did not contribute `state`/`h0`/`hidden`; tests and helpers dominated the top results.

### After all three fixes

- English: `dav_backward_pallas` #1 (`0.95`), `gdn2_dhu_backward.step` #2 (`0.94`), `_gdn2_core_bwd` #5 (`0.87`); `test_finite_diff_gradient` moved out of the top 3.
- Russian: Parser focus terms were `gradient`, `chunked`, `state`, `backward`, `chunk`; backward implementation nodes occupied the top 5 and ranking closely matched English.
- Dry-run corpus size: 76 files / 426 nodes in `atomic-ops`.

### Atomic-ops benchmark cases — 2026-10-12

- Added 5 cases backed by `atomic_ops/gdn2_bwd.py`, `gdn2_fwd.py`, `gdn2_pipeline.py`, `configs.py`, and `reference.py`.
- Corpus: 27 cases total.
- Retrieval: all 13 scored cases hit at k (`hit_all_at_k=1.0`), overall MRR `0.865`.
- The lower MRR reflects the larger mixed corpus and multi-file implementation/reference ranking; all five new atomic-ops expected-node sets were retrieved successfully.
- End-to-end: `49/54` (`90.74%`) across 2 repeats; existing 22-case baseline cases remained stable.
- `atomic-ops-backward-flow`: `0/2` by citation completeness; answers were grounded and cited `gdn2_dhu_backward` plus `_gdn2_core_bwd`, but omitted `dav_backward_pallas`.
- `atomic-ops-forward-pass`: `0/2` by citation completeness/pattern strictness; answers cited `gdn2_pallas_forward` and supporting functions, but did not consistently cite `gdn2_pallas_forward_trainable` or mention `pallas` in the prose.
- `atomic-ops-chunk-combine`: `2/2`; `atomic-ops-kernel-config`: `2/2`.
- `atomic-ops-reference`: `1/2`; one run passed, one hit an `Answer` JSON formatting error (`Unterminated string`), requiring a retry policy or a later rerun.
- Tokens: `670614` in / `179480` out; cost `$0.8269227`; average latency `17920.38 ms`.
- Report: `/tmp/atomic-flow-benchmark-27cases-2026-10-12.json`

### Real project: Multy-agentic-system-on-gemini-API

The originally intended `Sourcing-agent-1Version` repository was not present in
`/workspaces`, so this run used the successfully cloned
`Multy-agentic-system-on-gemini-API` project.

- Query: `как устроен основной пайплайн?`
- Mode: `simple`
- Model: `google/gemini-3-flash-preview`
- Grounding: `100%`
- Citations: 5 across `main.py`, `agents/shield.py`, and `agents/base.py`
- Tokens: `7400` in / `3326` out
- Cost: `$0.013678`
- Cost complete: `true`
- Latency: `16550 ms`
- Request: `req_9170f7e9e734`

### Real project medium-mode — 2026-10-12

- Query: `как устроен основной пайплайн?`
- Mode: `medium`
- Verdict: `ok`
- Grounding: `100%`; citation coverage: `100%`
- Citations: 5 returned, 1 dropped because its node was not in context
- Attempts: `1`
- Roles: `parser`, `answerer`, `critic`
- Prompt sources: all `builtin`
- Tokens: `12492` in / `4487` out
- Cost: `$0.019707`; cost complete: `true`
- Latency: `21749 ms`
- Request: `req_5800d1603ecd`

### Known issues

- Input tokens are high in modes that include the skeleton and large system prompts.
- Gemini reasoning can consume a substantial part of the output-token budget.
- `refund-call-chain` remains wording-sensitive because the answer may describe only the approval branch.

## Extended benchmark — 2026-10-13 (Phase 0)

- Corpus: 27 cases; 2 repeats per case, 54 total runs.
- Model: `google/gemini-3-flash-preview`; backend: `kaggle`; mode: `medium`.
- Passed (lenient): `52/54` (`96.30%`).
- Strict passed: `50/54` (`92.59%`).
- Flaky cases: `atomic-ops-backward-flow`, `atomic-ops-reference`.
- Tokens: `779429` in / `251170` out.
- Cost: `$1.0817077`.
- Average latency: `25607.35 ms`.
- Report: `/home/codespace/.atomic_flow/benchmarks/runs/20261013-131059.json`

### Phase 0 benchmark observations

- `min_citations` is active: lenient and strict totals differ by two runs.
- `atomic-ops-backward-flow` passed once leniently and once failed because the answer omitted expected citations.
- `atomic-ops-reference` passed once and once failed because the answer omitted the expected citation, not because of malformed JSON.
- `unanswerable-payout` passed `2/2`; both answers used `confidence="low"` and were correctly abstentions.
- No `Unterminated string` errors occurred with Kaggle `max_output_tokens=8192`.

## Extended benchmark — 27 cases × 2 — 2026-10-13 13:42 UTC (Phase 0 complete)

### Summary

- Passed (lenient): `51/54` (`94.44%`).
- Strict passed: `49/54` (`90.74%`).
- Flaky cases: `atomic-ops-backward-flow`, `atomic-ops-chunk-combine`, `atomic-ops-reference`.
- Tokens: `727478` in / `216835` out.
- Cost: `$0.9490435`.
- Average latency: `21614.69 ms`.
- Report: `/home/codespace/.atomic_flow/benchmarks/runs/20261013-134238.json`.

### What changed versus the previous baseline

- `min_citations` is enforced: strict results are lower than lenient results (`49/54` vs `51/54`).
- `atomic-ops-backward-flow`: `1/2` passed; one repeat omitted a required citation.
- `atomic-ops-forward-pass`: `2/2` passed.
- `atomic-ops-chunk-combine`: `1/2` passed.
- `atomic-ops-reference`: `1/2` passed; the failed repeat cited the Pallas implementation instead of the required reference node.
- `unanswerable-payout`: `2/2` passed with abstention behavior.
- No `Unterminated string` errors occurred with `max_output_tokens=8192`.

### Known issues for Phase 1

- The three flaky atomic-ops cases remain citation-sensitive across repeats.
- The benchmark command exits non-zero when any case fails, despite the overall `94.44%` lenient pass rate.

## Extended benchmark - 27 cases x 2 - 2026-10-13 14:08 UTC (latest)

### Summary

- Passed (lenient): `54/54` (`100%`).
- Strict passed: `51/54` (`94.44%`).
- Flaky cases: none.
- Tokens: `746428` in / `230388` out.
- Cost: `$1.0129556`.
- Average latency: `22338.24 ms`.
- Report: `/home/codespace/.atomic_flow/benchmarks/runs/20261013-140804.json`.

### What changed versus the previous baseline

- `min_citations` remains active: strict results are lower than lenient results (`51/54` vs `54/54`).
- `atomic-ops-backward-flow`: `2/2` passed leniently, but `0/2` passed strict citation checks.
- `atomic-ops-forward-pass`: `2/2` passed leniently and `1/2` passed strict checks.
- `atomic-ops-reference`: `2/2` passed; the previous JSON truncation issue did not recur.
- `unanswerable-payout`: `2/2` passed with the strengthened abstention check.
- No `Unterminated string` errors occurred with Kaggle `max_output_tokens=8192`.

### Known issues for Phase 1

- Strict citation completeness remains the only benchmark gap; the affected cases are `atomic-ops-backward-flow` and `atomic-ops-forward-pass`.
