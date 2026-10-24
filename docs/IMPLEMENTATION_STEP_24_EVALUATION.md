# Package 24: provider usage and reproducible Stage 4 preparation

## Anthropic accounting

The Messages adapter normalizes usage before parsing content and raises
`ResponseShapeError` carrying the received `ChatResult` when content is unusable.
Boolean/string/negative/missing counters are not coerced to known usage. Cache
read and creation counters are included in total input tokens, but are priced
separately from uncached input. Cache tariffs are trusted host constructor data
(`AnthropicCachePrices`), not model/project instructions or a built-in tariff
catalog. No model multiplier is guessed. Missing read/write prices or TTL
breakdown leave cost unknown, as do server-tool charges and a model override
whose configured price belongs to another model. Actual invoices remain the
provider's responsibility; these are declared host estimates.

Primary contract checked on 2026-10-24:
[Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
The documented token breakdown separates uncached input, cache reads and cache
creation. The page lists distinct cache TTL tariffs and model exceptions, which
is why this implementation requires explicit prices rather than universal
multipliers. Existing input/output prices must already account for applicable
model/context/service-tier pricing; this change does not discover those tariffs.

Validation uses mocked provider responses only; no provider credentials, live
requests or paid calls. Focused test evidence will be recorded separately.

## Stage 4

Preparation in progress. A prepared corpus or scripted calibration result is not
measured model quality. The independently sourced public issue corpus, executable
grader calibration, paired attempt schema and remaining environment/live-run
gates are recorded below as artifacts become verified.
