# Package 33 (P02 slice 2): name-level resolver and closure relink

Date: 25 October 2026. Source: clean on top of `44a8ea0` (branch
`experimental`). Model calls = 0.

## Implemented

- **`src/atomic_flow/graph/resolve.py`** — the resolver layer: `module_map`
  (dotted module → file, `__init__.py` packages included),
  `exports_by_file`, and `infer_file_edges`, which builds **inferred**
  cross-file edges from stored file facts (no re-parsing): `imports`
  file→file edges via progressive dotted-prefix module matching, and `calls`
  file→symbol edges only when the caller imports that file AND the leaf name
  is uniquely exported there. Ambiguous names and names with no imported
  exporter go to an explicit unresolved report — never a guessed edge.
  Inferred edges carry `facts_class: "inferred"` and the `name-resolver/1`
  provenance, distinguishable from extracted contains-edges.
- **`project.clean_build`** now emits inferred edges and an `unresolved`
  report alongside the extracted graph.
- **`delta.apply_delta`** closes the loop: one transaction now carries the
  extracted replacement, plus a relink of every inferred edge owned by the
  closure (the changed file ∪ its importers/importees, derived from the
  pre-change topology), recomputed from stored facts — still zero re-parses
  of other files. `GraphStore.commit` gained `drop_edge_ids`. The report
  exposes `relinked_files` and the unresolved list.

## Evidence (`tests/test_graph_resolve.py`, 3 passed; graph suites 20 passed)

- Clean build infers `app.py imports lib.py` and the unique
  `app.py calls lib.helper` edge; unresolved reports a call with no exporter.
- **Rename parity**: after `helper`→`assist` in `lib.py`, the importer's
  stale `calls` edge disappears and the whole store equals a clean build of
  the new bytes — with exactly one file parsed.
- **Delete parity**: removing `app.py` drops its outgoing inferred edges;
  store equals the clean build of the remaining files.

## Boundaries

Name-level resolution only: `from x import y` binds the file; attribute
calls, re-exports, wildcard imports and relative-import subtleties are not
resolved and land in the unresolved report when they surface. The closure is
importer/importee-local; a re-export chain across two hops can under-relink
until the fixpoint iteration lands (P02 continuation). No lexical postings
or retrieval cache invalidation yet.
