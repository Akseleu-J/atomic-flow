# Package 28 (P02 slice 1): file-level structural delta with parity gate

Date: 25 October 2026. Source: clean on top of `7b468ff` (branch
`experimental`). First P02 slice of [`PRODUCTION_ROADMAP.md`](PRODUCTION_ROADMAP.md).
Model calls = 0.

## Implemented

`src/atomic_flow/graph/project.py` and `src/atomic_flow/graph/delta.py`:

- **Clean build** — `clean_build(files)` adapts every file and builds
  name-level reverse indexes (`imports`, `raw_calls` → files) plus an exports
  index (leaf symbol name → node ids). `affected_by_name()` reports the
  name-level blast radius and is explicitly advisory: a name match is never
  treated as a resolved dependency.
- **File delta** — `file_delta(path, new_bytes|None, expected_old_digest)`
  produces the ops for one file (delete = `None` bytes). `apply_delta`
  validates a content CAS (refuses when the stored digest moved — "source
  changed since delta was built"), replaces every node owned by that path
  (stale symbols of the previous revision cannot survive), and commits under
  generation CAS. Reports instrumentation: `scanned_files=1`, `parsed_files`,
  `put/dropped` counts — a private-body edit parses exactly one file.
- **Transaction ordering** — `GraphStore.commit` now applies drops before
  puts (a put+drop overlap previously erased freshly written nodes; caught by
  the parity test).
- **Fallback rebuild** — `fallback_rebuild(store, files, reason)` rebuilds
  everything in one transaction and refuses an empty reason: broad
  recomputation is always explicit and recorded.

## Acceptance evidence (`tests/test_graph_delta.py`, 5 passed)

- **Delta/clean parity**: after a sequence (same-name overload added → new
  module → file deleted → body edit with line shift) the live store equals a
  clean build of the same bytes (node facts and edge ids compared).
- **Content CAS**: a delta built against an older digest is refused; the
  store keeps the newer revision exactly.
- **Bounded work**: monkeypatched adapter counter shows a body-only edit
  parses only the changed file in a three-file project.
- **Fallback discipline**: empty reason refused; "branch switch" rebuild
  restores parity.
- **Reverse index**: `helper` reports `app.py`; exports map resolves the
  symbol id.

Together with package 27: **16 graph-contract tests passed**; legacy
`test_text_and_tree.py` neighbours unaffected.

## Not claimed (open for P02 continuation)

- Resolver-true cross-file edges (imports/calls between files) and their
  affected-closure fixpoint; the current indexes are name-level only.
- Git diff / watcher ingestion, untracked files, branch-switch detection.
- Staged overlay graph and publication tie-in; lexical postings / retrieval
  cache invalidation; move detection (old/new id mapping).
- Crash-matrix (kill between fsync boundaries) — the commit is atomic
  tmp+fsync+replace, but the full injected-fault acceptance is P09 scope.
