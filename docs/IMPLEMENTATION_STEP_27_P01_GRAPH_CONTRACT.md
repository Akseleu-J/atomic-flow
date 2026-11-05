# Package 27 (P01): structural graph contract, store and AST adapter

Date: 24 October 2026. Source: clean on top of `ea69cf6` (branch
`experimental`). Implements the P01 slice of
[`PRODUCTION_ROADMAP.md`](PRODUCTION_ROADMAP.md): schema, identity, adapter
API, canonical snapshot fixtures and legacy migration. Model calls = 0.

## What was implemented

`src/atomic_flow/graph/` (new package):

- **`schema.py`** — versioned contract (`GRAPH_SCHEMA_VERSION = 1`): node
  kinds file/module/class/function, edge kinds contains/imports/calls/
  references, fact classes extracted/inferred/runtime_observed, fresh/stale
  fact status, tombstoned edges. Identity: `file:<posix-path>` and
  `sym:<path>::<Qualified.name>` with an explicit `#N` disambiguator —
  duplicate names are never silently merged, and a symbol id must derive from
  its own path+symbol (forged ids are refused). Line numbers are carried as
  revision coordinates only. Every node binds a source digest with an
  algorithm label.
- **`store.py`** — `GraphStore`: single-writer transactions with
  generation compare-and-swap (`GraphGenerationConflict` on moved bases),
  validate-before-commit (dangling active edges refused), atomic
  tmp+fsync+replace persistence, deterministic canonical JSON export
  (`export_canonical`) for parity checks. The roadmap's SQLite/WAL backend is
  a later, separately accepted change; the transaction API is
  storage-independent.
- **`adapter_ast.py`** — extracted-facts-only Python adapter
  (`python-ast/1`): file/class/function/method nodes with sha256 source
  digest, contains edges, raw import and call name facts retained for the P02
  reverse indexes. Parser failure is recorded as a fact, never raised.
  Resolution/inference is deliberately absent (P02).
- **`migrate.py`** — legacy `tree.json` (TREE_VERSION 4) → store: files,
  symbols, contains edges and resolved callees as `calls` edges; legacy
  content hashes keep an honest `legacy` algorithm label; unsupported legacy
  facts (imports list, fan counts, mtime, summary…) are listed in the report
  and remain only in the legacy tree, which stays authoritative until P02
  parity.

## Evidence

- `tests/test_graph_contract.py` — **5 passed**: stable identity for identical
  bytes, duplicate-name disambiguation (`Alpha.one` / `Alpha.one#2`), parser
  failure reporting, generation CAS refusal, canonical export determinism and
  reopen-parity, dangling-edge and forged-id refusal, migration fact
  preservation with an explicit unsupported-facts report.
- Neighbours `tests/test_text_and_tree.py` + `tests/test_index_commit.py` —
  13 passed (no regression to the legacy path).
- `ruff check src/atomic_flow/graph tests/test_graph_contract.py` — PASS;
  `compileall` — PASS.

## Boundaries (what this is NOT)

- No delta updates yet (P02): the adapter produces whole-file facts; callers/
  importers closure and reverse indexes are not implemented.
- No semantic facts, layers, tours, summaries (P03); no LLM anywhere.
- The store is JSON-file based; SQLite/WAL with its locking/corruption
  acceptance is future work. No claim of multi-host or power-loss durability
  beyond the fsync+replace pattern already used in this project.
- Migration marks legacy facts `stale` and does not rebrand legacy hashes;
  old tree remains the read path for existing tools until P02 parity gates
  pass. No UA source code was copied; the pinned reference is studied as data
  (see `UNDERSTAND_ANYTHING_REFERENCE_2026-10-24.md`).
