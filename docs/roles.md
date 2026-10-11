# Role protocol

Role outputs are JSON objects validated with strict Pydantic schemas in `atomic_flow.llm.schemas`.

| Role | Output | Purpose |
|---|---|---|
| Parser | `QueryIntent` | Classify task, scope, and focus terms |
| Refiner | `ContextPlan` | Select nodes for the first hard-mode context |
| Answerer | `Answer` | Produce a grounded response with citations |
| Critic | `AnswerCritique` | Assess support, citations, and missing context |
| Expander | `ContextPlan` | Add context for another hard-mode attempt |
| Reparser | `QueryIntent` | Revise interpretation after a wrong-answer verdict |

The user sees the Answerer's text, citations, confidence, follow-ups, verdict, and request artifact path. A Critic verdict is advisory model output and should not be treated as a formal correctness guarantee.
