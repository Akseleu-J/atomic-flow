from atomic_flow.benchmarking import benchmark_cases, run_retrieval_benchmark
from atomic_flow.llm import schemas
from atomic_flow.llm.prompts import EXAMPLES


def test_retrieval_benchmark_meets_honest_baseline():
    summary = run_retrieval_benchmark()["summary"]
    assert summary["hit_all_at_k"] >= 0.9 and summary["mrr"] >= 0.79


def test_prompt_examples_validate_against_schemas():           # the hint can never drift from the model
    for name, example in EXAMPLES.items():
        getattr(schemas, name).model_validate(example)


def test_case_ids_unique():
    ids = [c["id"] for c in benchmark_cases()]
    assert len(ids) == len(set(ids))
