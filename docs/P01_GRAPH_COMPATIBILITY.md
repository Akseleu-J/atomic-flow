# P01 — Understand-Anything graph compatibility contract

Continued and rechecked on 26 October 2026; historical filenames retained.

Reference: Egonex-AI/Understand-Anything at pinned
`e24fcf6d371bcfa529bc110bacb1bb43de1b8d33` (MIT), recorded in
UNDERSTAND_ANYTHING_REFERENCE_2026-10-24.md. This is an independently implemented
Python adapter/read port, not a copy of upstream runtime/prompts. No upstream code
was executed or imported. Semantic user operations are layered by P03 over this
contract; dashboard/JS-TS parity are later P13 scope from the original roadmap.

| UA concept | Atomic graph v1 mapping | Evidence/class |
|---|---|---|
| file | `file:path` | admitted content digest + algorithm |
| function/class | `sym:path::qualified[#N]` | revision line span, extractor/signature |
| module | Python file/package ownership and inferred import relation | file node; module alias is a view, no duplicate physical owner |
| config/document/pipeline | source resource symbol below file | path-classified metadata, semantic not_assessed |
| contains/imports/calls/references | typed edges with endpoint IDs | extracted/inferred/runtime_observed and freshness |
| summaries/tags/layers/tour/domain links | extensible semantic payload over source/node revisions | inferred; owned by P03, never source permissions |

Schema requires canonical project-relative POSIX paths, qualified symbols with
explicit duplicate disambiguation, finite JSON facts with exact freshness labels,
valid algorithm-tagged source digests and valid revision spans. Line numbers never
form identity. Same-name symbols remain separate. Rename/move is explicit old/new
mapping in P02 delta; ambiguity never merges silently.

GraphStore is a bounded canonical JSON backend behind generation/content CAS.
Every snapshot validates schema, duplicate keys/IDs and active endpoints. Defensive
read/write copies prevent external dictionary mutation from changing store state.
Same-generation on-disk byte change refuses stale commit. Missing/corrupt/schema
cache is rebuilt/migrated explicitly; no cache carries approval or read authority.
Storage links/junctions are refused. Atomic replacement is cooperating-writer
durability, not arbitrary host tamper/power-loss attestation.

`GraphReader` read API version 1 exposes nodes/get/edges/search/document/review and
legacy export. Current source inspection still requires source-tool admission and
fresh read before edit. External principal access requires matching GraphIdentity
and host GraphReadContext; all relative IDs are qualified by that namespace.
The optional no-context constructor is a trusted internal single-root adapter.

Legacy migration accepts a fresh empty store, validates distinct legacy IDs,
disambiguates duplicate symbols and preserves each supported legacy record as
stale data. Canonical export reproduces supported old records without claiming
legacy digests are sha256. Unknown kinds, dangling parent IDs, unresolved callees
and unsupported legacy facts are explicit report rows. They are not silently
converted to safe authoritative facts. Legacy source bytes/trees are never edited.

Python AST adapter never imports project code. Parse/encoding failures retain
file digest and explicit parse failure, not an empty successful graph. Resource
adapters expose source-only bounded metadata; JSON parse status is deterministic,
YAML/TOML prose is not executed. Methods, nested and short definitions survive
extraction. Graph review distinguishes supported structural validity from semantic
requirement verification and reports parser/stale/unresolved omissions.

Externally scoped stores persist their typed identity in the optional strict
identity envelope. A reader cannot relabel another project/root store; unbound
legacy stores require an explicit scoped rebuild before external access. Internal
unbound store documents retain the existing four-field schema-1 shape.

GraphReader.tree_nodes() adapts the validated graph snapshot to existing
TreeNode/ActivationIndex/search_tree interfaces. Legacy metadata/hash labels stay
in canonical export; runtime-compatible TreeNodes deliberately omit content_hash
so ToolContext independently hashes current source and still requires a complete
source read before any edit. Search results never create read records.
