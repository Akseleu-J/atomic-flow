# ADR-0001: immutable authorization envelope vs mutable plan

Date: 25 October 2026. Status: accepted design after independent P06 review;
runtime acceptance requires the corresponding exact-source CI evidence.
Context: [`ASSISTANT_HANDOFF.md`](ASSISTANT_HANDOFF.md) §7,
[`PRODUCTION_ROADMAP.md`](PRODUCTION_ROADMAP.md) P06,
existing opt-in envelope in [`agents/envelope.py`](../src/atomic_flow/agents/envelope.py)
and schema-2 approval in [`agents/plan_review.py`](../src/atomic_flow/agents/plan_review.py).

## Decision

Separate two artifacts with different change discipline:

1. **Authorization envelope (immutable per grant).** Fields the owner
   authorized: `allowed_paths` (exact), tool/command allowlist, executor
   kind, egress/network grants, credential scopes, `max_budget_usd`,
   deadline, call/replan caps, verification floors (verify set + required
   final checks), policy/runtime digest. Changing any field — widening OR
   narrowing — is a **new grant**: fresh review and explicit approval.
   Internal replan may never change those authority fields. Failure-history
   entries are audit counters, not renewed authority. In the current supported
   mode only built-in source/check tools and one writer are admitted; injected
   capabilities or additional network/credential grants require fresh review.
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

Schema-2 plan approvals now contain internal envelope version 2 with a finite
absolute `deadline_at = task.created_at + config.task.max_duration_seconds`
(default duration 3600 seconds). Review binds that deadline to its digest.
Replan/resume preserve the original value; a new plan does not reset time.
Trusted host UTC is the time authority. Checks are cooperative: known responses
are settled before expiry stops continuation; an already sent remote call may
still have an unknown outcome, which never permits replay.

Version-1 envelopes remain strictly decodable for inspection but cannot execute
autonomously without fresh review. They do not silently acquire a new deadline.
Legacy schema-1 exact-plan approval remains the default and gains no autonomous
authority. No persisted schema is loosened to accept unknown fields.

## Verification hooks

`envelope_preserved()` and `lineage_preserved()` return refusal reasons. Host
`inherit_constraints()` retains original goal IDs/caps/scopes/test links;
ambiguous equivalence requires review. These predicates supplement source/runtime,
deadline, accounting/effect, tool-policy and publication gates; none replaces them.
`check_deadline()` runs at resume, step/tool/check, physical-call admission and
post-settlement, replan/grant-renewal and publication boundaries. Behavioral
matrix: handoff §7 and source-bound acceptance mapping. Tests include
`test_internal_replan.py`, `test_p06_constraints.py`, `test_p06_deadline.py`,
`test_p06_public_repair.py`, `test_envelope.py` and `test_replan_lineage.py`.

## Consequences

Internal in-envelope repair continues without a question (P06 goal), while
every widening provably pauses. Cost: replan acceptance logic must be
maintained against both predicates; hierarchy bugs fail closed into pauses,
never into widened authority.
