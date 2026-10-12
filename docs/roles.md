# Role protocol

Outputs are JSON validated by `atomic_flow.llm.schemas` (unknown keys ignored, invalid enums coerced; one repair round on bad JSON).

| Role | Output | Purpose |
|---|---|---|
| Parser | `QueryIntent` | Task, scope, English focus terms (also bridges non-English questions) |
| Refiner | `ContextPlan` | Pick nodes for the first hard-mode context |
| Answerer | `Answer` | Grounded response with citations |
| Critic | `AnswerCritique` | Support, citations, missing context |
| Expander | `ContextPlan` | Add context for another attempt |
| Reparser | `QueryIntent` | New interpretation after `wrong_answer` (re-ranks retrieval) |

The critic is a model opinion. Deterministic checks (invalid citations dropped, backticked identifiers missing from context) can downgrade an `ok`.
