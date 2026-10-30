# P07: proposed allocation within the existing $20 authorization

**Status: PROPOSED_UNFROZEN / NOT_LAUNCHED.** Date: 27 October 2026.
This document does not change the active budget plan, owner ceilings, model,
experiment profile or acceptance requirements. It authorizes no additional spend
and is not evidence that any paid experiment has run or that P07 is closed.

## Proposed allocation

The candidate profile allocates **84 arms × $0.22 = $18.48**:

| Experiment | Independent task assumption | Arms | Maximum allocation |
| --- | --- | --- | --- |
| Main matched comparison | 30 pinned tasks, baseline + full Atomic | 60 | $13.20 |
| Fresh holdout | 6 newly selected tasks, baseline + full Atomic | 12 | $2.64 |
| Controlled ablations | 3 registered development tasks × 4 separate ablations | 12 | $2.64 |
| Total proposed new arm allocation | | 84 | **$18.48** |

The first pilot pairs would count once among the 30 main pairs. They would not
be additional paid runs. Each ablation disables exactly one registered feature
on a predeclared development task; its complete Atomic comparator must already
exist under the same frozen model, permissions, caps and ordinary/hidden checks.
The four proposed factors are graph retrieval, semantic layer, task DAG and
critic. Their actual definitions and eligible tasks still need registration.

| Fixed authorized owner | Main comparison | Fresh holdout | Ablations | Proposed new spend | Existing new-spend ceiling |
| --- | --- | --- | --- | --- | --- |
| owner2: `akseley` | 15 pairs: $6.60 | 3 pairs: $1.32 | 1 task × 4 arms: $0.88 | **$8.80** | $9.50 |
| participant3: `umirbayulgaisha` | 15 pairs: $6.60 | 3 pairs: $1.32 | 2 tasks × 4 arms: $1.76 | **$9.68** | $10.00 |

These are prospective disjoint cohorts, not account fallback. Freeze each task
and experiment to its exact owner before scheduling. Quota refusal, transport
failure or an unknown outcome must not trigger account rotation, automatic
redistribution or an automatic retry.

The aggregate accounting would be:

| Item | Amount |
| --- | --- |
| Proposed new arm allocation | $18.48 |
| Previously booked spend | $0.035611375 |
| Existing unallocated reserve | $0.464388625 |
| Total accounted allocation, including reserve | **$18.98** |
| Remaining within the existing $20 ceiling | **$1.02** |

The remaining $1.02 corresponds to $0.70 below owner2's ceiling and $0.32 below
participant3's ceiling. Neither that headroom nor the existing reserve is an
automatic rerun allowance. An unknown physical attempt keeps its reservation
until reconciliation; it is never silently priced at zero.

## Preconditions before any inference

1. Complete ordinary-check runner admission for the actual task environments,
   the public full Atomic journey and a capable iterative read/edit/check
   baseline. A source-only diagnostic or calibration success alone is
   insufficient. Preserve independent hidden grading in a fresh isolated worker.
2. Freeze a new explicit low-cap profile and its source/configuration hashes
   before inference. **$0.22 per arm is not equivalent to the former $0.80
   profile.** Budget exhaustion, no candidate and uncompleted cases remain in
   the declared denominator; smaller caps must not be represented as equal
   capability to the older experiment.
3. Reverify the exact authorized model/catalog identity and provider route.
   The intended model remains `anthropic/claude-sonnet-5@default`; no substitute
   is selected here. The existing reference input/output rates of $2/$10 per
   million tokens are **not a provider billing guarantee**. A hard spending claim
   requires validated billing limits or conservative enforceable reservations.
4. Fix equal per-arm maximum physical calls, input/output tokens, elapsed time,
   retry rules, permissions, source visibility and checker authority for both
   main arms and holdout arms. Count all model roles, including planning,
   replanning, parser, critic, repair and semantic-index inference. Index and
   inference costs belong to the complete arm/aggregate accounting; they are
   not excluded overhead. Actual usage may differ and must be reported.
5. Freeze task IDs, owner assignments, alternating arm order, stopping rules,
   unknown-outcome handling, hypothesis and evidence schema. A shared JSON
   ledger remains a single-writer mechanism. Parallel paid calls require a
   separately accepted transactional reservation broker; independent workers
   cannot race read/reserve/write operations against one ledger.
6. Preserve every physical attempt, failure, timeout, reservation and reconciled
   charge. Do not automatically replay uncertain inference or scheduling.
   Report known cost separately from unknown reserved cost and retain all
   failures in cost-per-success and end-to-end utility metrics.

## Fresh holdout remains unselected

Six holdout tasks are a **budget assumption only**. No IDs or repositories are
chosen by this document. A trusted metadata-only selection process must first
freeze the upstream dataset revision/hash, exclusions for all previously exposed
tasks, repository split, selection algorithm/seed and replacement rule. Problem
statements, gold/test patches and solutions must remain outside the planning
context until the selection and experiment profile are fixed. A separate oracle
custodian calibrates those environments without disclosing solutions to the
generator. Blocked selections stay recorded under the frozen selection rule.

If the dataset cannot provide a genuinely new repository split, label the
experiment as within-repository holdout. Six tasks are an exploratory allocation,
not a powered confirmation study or evidence of competitive superiority. A
larger confirmation design or higher caps would require a separate decision
within an explicitly authorized budget; this proposal raises no ceiling.

The active budget plan remains unchanged. P07 remains OPEN until its actual
runner, matched-pair, holdout, ablation, provenance and independent-review gates
are satisfied by measured evidence.
