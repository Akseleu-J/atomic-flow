# Package 29 (P03 slice 1): deterministic understand layer

Date: 25 October 2026. Source: clean on top of `2a1ec5e` (branch
`experimental`). Structural part of the P03 stage of
[`PRODUCTION_ROADMAP.md`](PRODUCTION_ROADMAP.md). Model calls = 0; no LLM
anywhere in this package.

## Implemented (`src/atomic_flow/graph/semantic.py`)

- **`layers(files)`** — deterministic top-package grouping of files, bound to
  per-file source digests and extractor version `structural-semantic/1`.
- **`entry_points(files)`** — files no other project file mentions by module
  name; progressive dotted-prefix matching (`tools.util.scrub` → `tools/util.py`).
  The method is labeled name-level, not a resolution claim.
- **`tour(files)`** — deterministic walk order (entry candidates first),
  positions and per-step rationale; explicitly notes that narrative tours
  require the later LLM stage.
- **`invalidated(current_digests, artifact)`** — exact list of files whose
  digest moved since a semantic artifact was produced: the P03 staleness
  contract, so a body edit invalidates the artifact precisely, without
  guessing.

All artifacts embed `source_digests` so downstream consumers can refuse stale
facts instead of trusting timestamps.

## Evidence

`tests/test_graph_semantic.py` — 4 passed: deterministic layers with digest
binding, correct entry-point direction (importers excluded, imported files
excluded; bug in the first draft inverted this and was caught by the test),
entry-first tour ordering, digest-change invalidation limited to moved files.
Combined graph suites: **20 passed**; `ruff` clean; `compileall` clean.

## Not claimed

- No summaries/tags/explanations/domain flows — those are the admitted-model
  stage with cache keys (source deps, extractor/prompt/model version); this
  package deliberately ships zero model calls and zero fabricated narrative.
- No CLI/MCP exposure yet; no architecture-layer inference beyond top package;
  entry points are heuristic and can both over- and under-report until the
  P02 resolver exists.
