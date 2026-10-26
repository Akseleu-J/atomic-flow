# P00-P06 integration review — 26 October 2026

Reviewer: independent integration subagent `integration_peer`; base checkout
`995e3bb5cdc07bec4b56005c937187a1408645e6` with the shared integration patch.
This is a source review of the files listed below, not a whole-system independent
certification. No pytest, paid inference, external-project execution, installation,
commit or push was performed by this reviewer. The parent owns final execution,
source/CI sealing and the chapter acceptance decision.

The reviewer subsequently implemented two focused fixes at the parent's request.
Those fixes are **self-reviewed by their author** and require parent review and
execution; independence of the initial finding does not make its author's patch
independently accepted. The source hashes below bind this exact review snapshot;
later formatting or implementation changes require an explicit hash comparison.

## Findings and disposition

1. **P1 — stale semantic summaries after dependency-topology expansion.**
   `graph_operation` checked only dependency digests recorded in the supplied
   artifact. If a consumer previously imported a missing provider, then adding
   that provider left the consumer and every old recorded digest unchanged. An
   old summary could therefore retain `fresh` despite new resolved context.
   The same issue affected an added unresolved file, because enrichment's
   conservative dependency policy then expands to all admitted files.
   Fixed here by deriving the required current closure from the current graph;
   unresolved references require all current admitted files. Existing explicit
   extra host dependencies remain allowed and are still digest-checked. Missing
   required bindings refuse semantic reuse. Regression file:
   `tests/test_p03_semantic_topology.py` (three cases). The parent reported these
   cases passing in its focused run; the parent retains the actual JUnit identity.

2. **P1 — initial and fallback graph publication omitted the final source check.**
   Ordinary owned delta sync already checks the source immediately before CAS;
   initial creation and full fallback built from an earlier snapshot and committed
   without the equivalent post-parse check. A source change during parsing could
   therefore be recorded as successful stale graph publication. Fixed here by a
   final admitted-snapshot comparison and cancellation boundary before creation;
   fallback receives equivalent `source_check`/`cancel_check` callbacks executed
   after parsing and before CAS. A failed fallback preserves the prior graph.
   `tests/test_p02_sync_snapshot.py` adds creation drift and fallback drift cases.
   The parent subsequently reported both cases passing in its coordinated
   52-case selection; the parent retains the actual JUnit/source identity.
   This is a bounded race guard, not atomicity against an arbitrary uncooperating
   writer after the final check.

3. **P1 earlier finding — partial paginated source was eligible for citation PASS.**
   Reviewed the parent's correction in `llm/orchestrator.py`: eligibility now uses
   `ToolContext.read_coverage(path, current_hash, node_id)["read_complete"]`, rather
   than a rendering's `source_complete` flag alone. The coverage implementation
   combines delivered ranges, binds their source hash and node/file scope, and
   requires a complete source representation. `citation_span` still checks the
   exact current bytes and line bounds. The regression with an unshown line 202
   is present in `tests/test_p04_span_visibility.py`. This correction is accepted
   by source inspection; execution evidence belongs to the parent.

4. **P2 — default pytest `*_test.py` discovery was absent from applicability.**
   `completion.py` recognizes `tests/` and `test_*.py`, whereas pytest also finds
   `*_test.py` by default. The parent subsequently confirmed adding the suffix
   case; that later patch is outside this captured source review. No change to
   completion code was made by this reviewer. Nonstandard discovery
   configuration requires explicit final-check policy and is not established by
   this filename heuristic.

5. **Acceptance design discrepancy — selected SQLite design versus JSON backend.**
   `PRODUCTION_ROADMAP.md` lines 85-95 select an initial SQLite/WAL graph store.
   The implemented `GraphStore` is canonical JSON with cooperating-writer
   generation/content CAS, and its docstring explicitly defers SQLite. Numbered
   P01 criterion 4 specifies the transaction/API, which can be reviewed separately,
   but the selected backend design has not thereby been implemented. Before a
   full-roadmap completion claim, resolve this with an explicit reviewed backend
   decision or keep that architecture obligation open. This report does not
   silently reinterpret a deferred backend as delivered SQLite.

## Reviewed contracts

- P01 strict wire validation, duplicate ID/key refusal, finite JSON and digest/
  coordinate validation; defensive snapshots; persisted generation plus byte CAS;
  typed project/root/principal/policy read context; legacy read compatibility.
  The no-context reader remains explicitly trusted in-process access, not remote
  authentication. Host object mutation is not treated as an adversarial boundary.
- P02 private staged multi-file overlay and one visible CAS; incoming edges to
  surviving IDs are preserved while owned outgoing edges are replaced; export/
  module changes drive conservative consumer relinking; external Git/watch hints
  are followed by admitted reconciliation. Relocation is advisory, and ambiguous
  matches do not grant read/write authority. Owned tree delta only reads supplied
  trusted paths; external writers still require the full audit boundary.
- P03 evidence validation, separate semantic hashes, conservative dependency
  closure and transactional cache publication; explicit provider wraps the shared
  physical-call ledger without introducing retries. Evidence presence does not
  prove narrative truth. SDK/read operations perform final snapshot revalidation.
- P04 read coverage and current citation eligibility; the completion gate checks
  the final manifest before/after execution and distinguishes unavailable tests
  from passing tests. Declared semantic criteria remain explicitly not assessed.

No additional definite blocker was found in the inspected P01 schema/identity/CAS
mechanics. This does not cover every adapter configuration, process crash state,
OS matrix or enterprise authorization integration. P00 inventories and P05/P06
autonomy mechanisms were not independently re-audited in full by this reviewer;
their prior dedicated reports and final integrated evidence remain necessary.
Production quality, scale, release and isolated-worker acceptance are outside
this review's acceptance claim.

The parent subsequently reported fixes for absent-tree `ToolContext.refresh`,
zero-node context coverage, pytest suffix discovery and a source-bound owned
move/edit journey, plus an explicit JSON-backend ADR replacing the initial SQLite
selection. Those later implementation/ADR bytes were **not independently reviewed
here**. In particular, the captured context/completion/relocation hashes below
may differ from final integration; retain the old capture rather than implying
that a review hash covers later uninspected changes.

## Exact raw working-file SHA256 at review capture

Hashes cover actual working-file bytes, including line endings. They are not Git
commit IDs and are not a substitute for the final normalized source CI manifest.

| Reviewed file | SHA256 |
|---|---|
| `src/atomic_flow/graph/identity.py` | `15c8aaa4575ab6fea22e0a8749907510d77bf4b4e8914cae9d8106ae4f0fff69` |
| `src/atomic_flow/graph/schema.py` | `670ab0d517e4303e69bfa33a0d681bf05f145608228b5562cc563c85e82942e3` |
| `src/atomic_flow/graph/store.py` | `2184c1f549a5234dc68ddb49025ca20344f8c37854e776bb00553008f7caf886` |
| `src/atomic_flow/graph/compat.py` | `0e0b7b40b9b16665c98140fb10ad3040a1bbbbeb28929bb629a5493d337d6bd8` |
| `src/atomic_flow/graph/adapter_ast.py` | `4c9e48ab7da72d86c5555d728cf5a57b961517e9d1d41713a8b17dcf9d7c90c0` |
| `src/atomic_flow/graph/adapter_resource.py` | `ba6cb7790b231301809ca1bc42694db7f5701f83bf493b6f80b51f7e6b9a0409` |
| `src/atomic_flow/graph/project.py` | `4b963aac88e5bdb1e3bb59425c45f7d25e52f7a5539ff4c43d6ee4c4de4bb610` |
| `src/atomic_flow/graph/resolve.py` | `52a0ea782f1f952f14d567f640f77e14e570c1454eff758a4f36ac021cbead27` |
| `src/atomic_flow/graph/delta.py` | `64601771f1c7699b847822bf7b17fb98b41dfca2e6f7bbb3819e7a1103ae605c` |
| `src/atomic_flow/graph/ingest.py` | `9a29346d7486986a8cd3dd180ae637a20f820b904cc297a355a032c6616d94db` |
| `src/atomic_flow/graph/relocation.py` | `149483ffff419add0c7106924ec44d142e2b9979f178add2f113ae0befe702d3` |
| `src/atomic_flow/graph/semantic.py` | `f7153ca926be7899053789c10a97949ede6faeb15edb3755ed9d03acae5ece25` |
| `src/atomic_flow/graph/enrichment.py` | `842d59ad310ba82b17aed177f78a9c16090a922a1fcbe657e3fa1eee0d01002a` |
| `src/atomic_flow/graph/operations.py` | `201742bee6089200393171100b593e25e86885f4f32512f0dd6612711dc628d7` |
| `src/atomic_flow/graph/provider.py` | `7a07cacb63eaa0a10afb1db1ecb4f75fb2cdf5b0fabb5eef1b2ceda44b8ec63b` |
| `src/atomic_flow/graph/sync.py` | `3b89455d85e06341ab394971bb8e66a3234977170978c9bb9ddf01de60802f3e` |
| `src/atomic_flow/agents/context.py` | `64fd65c55e9239e1791b28874b602c1e116ef70829644c6af6a0d3e0a67380d3` |
| `src/atomic_flow/agents/completion.py` | `5142da58877aae23cd959d95bec31452c3470202e91359f8f9d28b72132cded7` |
| `src/atomic_flow/llm/orchestrator.py` | `1571c9c89be57f0ed4f81e5e5e21ebabe461cffd4ede38bb130162be7ab9291a` |
| `src/atomic_flow/tree/builder.py` | `61dce0b10011ee6e6eef85691a6ff5bb2fecd1c7f9c9a4e732630876a509d95d` |
| `tests/test_p01_contract.py` | `e4704c00eb205bc18e378808e2538e600f996a1b43d45a52e2d56f0a4c183126` |
| `tests/test_graph_sync.py` | `be877d93b0b350b82e2e56d5f8330bf40e31fdf63609aa0faad9170fe4d83c0c` |
| `tests/test_p04_span_visibility.py` | `8562f32fcfc6350c9471306a4a41419d2b9b9b4cedce8a0986aa839cf4c006cf` |
| `tests/test_p03_semantic_topology.py` | `6e9e9e8cea6767e0d9102f964fe6bb0300eaa2780c260f7f81e3fb4d68ce842d` |
| `tests/test_p02_sync_snapshot.py` | `f23ad0bbd8a3cb11c721dc1077f8dd11fc106a1abf4034262ef980e30264e45c` |
