# P06 chapter status (DRAFT — independent review pending)

Filled per [ASSISTANT_HANDOFF.md](ASSISTANT_HANDOFF.md) §13. Decision line
stays OPEN until the independent review findings are addressed.

```text
Глава / поддерживаемый scope:
  P06 bounded autonomy: opt-in envelope (foundation by packages 35–39) +
  slices 2–4 (lineage predicate, failure classification table, publication
  graph delta-sync). Full hierarchical auto-replan wiring: OPEN.

Base SHA → result SHA / PR:
  a566675 → e43b45b (branch experimental, PR №2 draft).
  Runtime-relevant: 35573f6 (lineage+failure table), 20913a5/e43b45b
  (graph sync + Task.graph_sync), on top of runtime 91d22e2.

Что изменилось и какое поведение теперь поддержано:
  - ADR-0001: immutable authorization envelope vs mutable plan.
  - lineage_preserved(): criteria/scope/ancestor-cap/required-checks obligations
    fail closed (pure; not yet wired into the live replan path).
  - failure_policy.classify(): explicit stop/continue table; unknown kind stops.
  - Publication hot path delta-syncs .atomic_flow/graph.json (content-CAS +
    closure relink; reasoned fallback; failures recorded, never flip a
    completed publication). Task.graph_sync field; budget_events untouched.
  - §7.7 scripted journey green: edit→checks→publish→graph parity.

Local checks:
  tests/test_replan_lineage.py 6, test_failure_policy.py 15,
  test_graph_sync.py 3, test_p06_journey.py 1 — PASS.
  Neighbours: task/task_execution/internal_replan/task_changeset/
  capability_composition/composition_acceptance — 74 passed total across
  focused runs; selections do not overlap in counted case IDs.

CI:
  push 40193302207 + 40193302211 and PR 40193305969/40193306020 on e43b45b:
  both workflows SUCCESS. (Interim 20913a5 was RED — budget_events pollution
  caught by CI, fixed in e43b45b; history preserved.)

Real executor / protocol / live model evidence: отдельно:
  Scripted only; live SymPy diagnostic session stopped after 3 infrastructure
  failures ($0.001608 of $1.80; harness ValidationError fixed in 7ab12d4,
  then host MemoryError — §10 machine boundary). No quality claim.

Измерения: graph sync on 2-file project: parsed_files=1 per delta,
  relink without reparse (test_graph_sync). SymPy-scale hot-path numbers:
  not measured (MemoryError boundary).

Независимый review: PENDING (reviewer session running; self-review не считается).

Известные ограничения и внешний blocker:
  - lineage/failure-table not yet consulted by task_loop replan path
    (schema-2 checks remain authoritative) — wiring is the next slice.
  - crash-during-sync and malicious-instruction-during-repair rows not yet
    scripted; graph sync does not feed retrieval.
  - live diagnostic blocked by host RAM (3.4 GiB) for SymPy-scale runs.

Решение: OPEN — awaiting independent review + remaining slices above.

Следующая глава и её prerequisites:
  finish P06 wiring + adversarial matrix rows, then P07 (needs E01 honest
  verification and accepted isolated execution before untrusted code).
```
