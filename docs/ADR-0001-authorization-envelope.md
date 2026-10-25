# ADR-0001: immutable authorization envelope vs mutable plan

Date: 25 October 2026. Status: proposed (P06, first assignment item).
Context: [`ASSISTANT_HANDOFF.md`](ASSISTANT_HANDOFF.md) §7,
[`PRODUCTION_ROADMAP.md`](PRODUCTION_ROADMAP.md) P06,
existing opt-in envelope in [`agents/envelope.py`](../src/atomic_flow/agents/envelope.py)
and schema-2 approval in [`agents/plan_review.py`](../src/atomic_flow/plan_review.py).

## Decision

Separate two artifacts with different change discipline:

1. **Authorization envelope (immutable per grant).** Fields the owner
   authorized: `allowed_paths` (exact), tool/command allowlist, executor
   kind, egress/network grants, credential scopes, `max_budget_usd`,
   deadline, call/replan caps, verification floors (verify set + required
   final checks), policy/runtime digest. Changing any field — widening OR
   narrowing — is a **new grant**: fresh review and explicit approval.
   Internal replan may never touch it.
2. **Plan/frontier (mutable, host-verified).** Step decomposition,
   descriptions, ordering, research steps, bounded repair steps. The host may
   replace the plan during execution **only** by passing
   `envelope_preserved()` plus the P06 lineage checks below; otherwise the
   task pauses for fresh review (today's behavior).

## Lineage rules for hierarchical replan (the part P06 adds)

- A replan that creates a subgoal/namespace inherits the parent goal's
  obligations: scope paths, budget caps (ancestor caps still apply —
  `task_budget.py` ancestor-chain contract), criteria links and remaining
  deadline. A new namespace never resets spend or drops a criterion.
- Model-text similarity between old and new step descriptions is NOT formal
  equivalence; only host-verifiable fields count (paths, tools, verify kinds,
  depends-on closure).
- Obligations are lost (⇒ mandatory pause): a criterion no longer linked to
  any step, a required final check removed, scope paths absent from the new
  frontier, or budget/deadline exceeded.

## Mandatory stop cases (never auto-continue)

Scope/tool/command/executor/egress expansion; verification weakening or
oracle substitution; criteria loss; budget/call/replan cap reached;
no-progress guard tripped; runtime/policy/source/grant digest drift; unknown
charge/effect; cancelled-before-send is free, after-known-response keeps
cost and adds no effects; crash/resume grants no new authority and replays
no completed external effects; repository instructions cannot widen
anything.

## Migration / compatibility

Existing persisted schema-2 approvals already carry envelope fields; the
envelope view is derived (read-only projection), so no on-disk migration is
required. Legacy schema-1 tasks keep the exact-plan default. Public schemas
gain only optional fields; strict decode stays.

## Verification hooks

`envelope_preserved()` (pure) + new `lineage_preserved(old_task, new_plan)`
(pure, P06 implementation) are the only gates; both return explicit reasons
that the pause path must show verbatim. Behavioral matrix: handoff §7
table; tests live in `tests/test_internal_replan.py`,
`tests/test_envelope.py`, new `tests/test_replan_lineage.py`.

## Consequences

Internal in-envelope repair continues without a question (P06 goal), while
every widening provably pauses. Cost: replan acceptance logic must be
maintained against both predicates; hierarchy bugs fail closed into pauses,
never into widened authority.
