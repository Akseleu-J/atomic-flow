"""Kaggle Benchmarks task: refund eligibility boundary conditions."""

import kaggle_benchmarks as kbench


@kbench.task(name="atomic-flow-refund-window", description="Checks grounding in an exact refund eligibility rule.")
def refund_window(llm):
    context = '''
    def is_refundable(status: str, days_since_purchase: int) -> bool:
        return status == "paid" and 0 <= days_since_purchase <= 30
    '''
    response = llm.prompt("State the exact conditions for a refund. Do not infer any other rules.\n" + context)
    kbench.assertions.assert_contains_regex(r"(?i)paid", response, expectation="The answer requires paid status.")
    kbench.assertions.assert_contains_regex(r"(?i)30", response, expectation="The answer states the 30-day upper limit.")
    kbench.assertions.assert_contains_regex(r"(?i)0", response, expectation="The answer states that negative day counts are rejected.")


refund_window.run(kbench.llm)
