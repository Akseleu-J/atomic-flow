# Package 30 (P04 slice 1): context pack contract with inclusion reasons

Date: 25 October 2026. Source: clean on top of `a1d0d1d` (branch
`experimental`). First P04 slice of [`PRODUCTION_ROADMAP.md`](PRODUCTION_ROADMAP.md).
Model calls = 0.

`src/atomic_flow/graph/impact.py::context_pack(files, goals, byte_cap)` — an
explicit read plan for a goal: goal files first, then files that name-mention
the goals' exported symbols or module, each entry carrying its inclusion
reason and byte size; a byte cap marks (never silently drops) overflow
entries as truncated. Unknown goals are refused. Name matches stay advisory —
no resolution claim. This is the structural answer to the pilot failure class
"the model never learned what to read": the host can now hand the agent a
bounded, explainable read order.

Evidence: `tests/test_graph_impact.py` — 3 passed (goal-first ordering with
reasons, name-match blast radius inclusion, byte-cap truncation marking,
unknown-goal refusal); combined graph suites 23 passed; ruff clean. Not
claimed: wiring into the live agent tool loop (next P04 slice), semantic
enrichment, resolver-true impact.
