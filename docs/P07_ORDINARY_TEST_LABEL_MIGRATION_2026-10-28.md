# P07: remaining ordinary-test label migration

28 October 2026. Implementation map from independent read-only review.
Status: planned; this document does not claim the migration is implemented.

The public command=check already selects only an immutable host obligation;
actual runner observations distinguish pytest from sympy-native. Existing plan,
step and failure labels still carry compatibility spelling pytest. Do not rewrite
persisted legacy strings, approvals or their digests.

## Atomic implementation package

- agents/planner.py: add ordinary_test to VerifyMethod and describe a complete
  host obligation. Runtime must reject use without that obligation.
- config.py: add final_check ordinary_test, retaining syntax default and pytest.
- agents/task_loop.py: enforce host obligation before model/execution in run,
  review, approve, resume and replan. Route only through run_obligation_check;
  support the new rollback verification key without unbound fallback.
- agents/envelope.py: ordinary_test floor2 and both test-label execution checks;
  preserve original stored labels/digests and prevent downgrade or new authority.
- agents/step_evidence.py: accept the new label, bind its exact contract string;
  retain byte-identical legacy receipt behavior.
- agents/verify.py: bound branch reports syntax/ordinary_test; unbound stays pytest.
- agents/tools/file_ops.py, edit_node.py and agents/result.py: preserve the new
  verification key in success, deletion, rollback and bounded summaries.
- agents/completion.py: ordinary_test without obligation refuses before any
  unbound fallback. A bound final check remains the complete host obligation.

Version2 typed failure evidence is a separate narrow package: actual runner,
completed-negative flags and obligation/spec/receipt digests, with strict exact
schema and current source binding. Version1 legacy schema must not be expanded.
Advisory full_pytest/targeted_pytest names and campaign profiles are later changes.

## Required acceptance

Test new schema acceptance, unchanged defaults and legacy digests; envelope
floors, execution authority and downgrade rejection; missing-obligation refusal
before model/subprocess; native failure/repair and unavailable/skip/bootstrap/
stale refusal; edit and rollback verification propagation; complete final gate.
Then run the real native SymPy journey with the new label while retaining the
legacy compatibility journey. No paid comparison or P07 closure follows from
label migration alone.
