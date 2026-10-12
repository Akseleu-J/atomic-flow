"""Kaggle Benchmarks task: refund eligibility boundary conditions."""

import kaggle_benchmarks as kbench


@kbench.task(name="atomic-flow-refund-window", description="Checks grounding in an exact refund eligibility rule.")
def refund_window(llm):
    context = '''
    def is_refundable(status: str, days_since_purchase: int) -> bool:
        return status == "paid" and 0 <= days_since_purchase <= 30
    '''
    response = llm.prompt("State the exact conditions for a refund. Do not infer any other rules.\n" + context)
    kbench.assertions.assert_contains_regex(r"(?i)\bpaid\b", response, expectation="The answer requires paid status.")
    kbench.assertions.assert_contains_regex(r"\b30\b", response, expectation="The answer states the 30-day upper limit.")
    # v0.1 used r"(?i)0", which matches the "0" inside "30" and therefore always passed.
    kbench.assertions.assert_contains_regex(
        r"(?i)(\b0\s*(<=|≤|<|to|and|-|–)|>=\s*0|≥\s*0|non-?negative|not negative|at least (0|zero)|zero or (more|greater))",
        response, expectation="The answer states the lower bound (days >= 0, i.e. negative day counts are rejected).")


refund_window.run(kbench.llm)
