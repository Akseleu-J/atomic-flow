"""Kaggle Benchmarks task: follow a helper call across source files."""

import kaggle_benchmarks as kbench


@kbench.task(name="atomic-flow-refund-call-chain", description="Checks whether a model follows a cross-file function call.")
def refund_call_chain(llm):
    context = '''
    # invoice_service.py
    def is_refundable(status, days_since_purchase):
        return status == "paid" and 0 <= days_since_purchase <= 30

    # refund_policy.py
    def refund_message(status, days_since_purchase):
        if is_refundable(status, days_since_purchase):
            return "refund approved"
        return "refund denied"
    '''
    response = llm.prompt("Explain how refund_message chooses its result, citing the rule it calls.\n" + context)
    kbench.assertions.assert_contains_regex(r"(?i)is_refundable", response, expectation="The answer follows the cross-file helper call.")
    kbench.assertions.assert_contains_regex(r"(?i)approved", response, expectation="The answer names the approval result.")
    kbench.assertions.assert_contains_regex(r"(?i)denied", response, expectation="The answer names the denial result.")


refund_call_chain.run(kbench.llm)
