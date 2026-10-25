# Package 34 (P06 slice 1): replan envelope predicate and no-progress guard

Date: 25 October 2026. Source: clean on top of `59ee75f` (branch
`experimental`). Model calls = 0.

`src/atomic_flow/agents/envelope.py` — two pure functions, no runtime wiring
yet (the integration commit will call them from the replan path):

- **`envelope_preserved(approval, new_steps, failure, budget_spent)`** — the
  formal check an internal replan must pass before the host may continue
  without a fresh human question: plan may change, authorization envelope may
  not. Refuses: tools outside the approved plan's tool set; introducing a
  mutating tool into an approved read-only plan; `verify_with` kinds outside
  the approved set; budget at/over the approved cap; tripped no-progress
  guard. Returns explicit reasons for every refusal — the pause path can show
  them verbatim.
- **`no_progress(approval, failure, limit=2)`** — oscillation detection on
  recurring first-line failure signatures from the approval's
  `replan_failures` history (recorded by the integrating caller).

Evidence: `tests/test_envelope.py` — 6 passed (in-scope replan preserved;
tool widening, mutating-tool introduction, new verify kind, budget cap and
recurring failure each refused with named reasons). Ruff/compile clean.

Not claimed: the replan path does not call this yet — internal replans still
always pause for fresh review (unchanged behavior); auto-accept integration
plus `replan_failures` recording is the next slice. No authority is derived
from model text anywhere here.
