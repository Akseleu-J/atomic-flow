"""Kaggle Benchmarks task: grounded explanation of an invoice method."""

import kaggle_benchmarks as kbench


@kbench.task(name="atomic-flow-invoice-subtotal", description="Checks whether a model explains a Python method from supplied code.")
def invoice_subtotal(llm):
    context = '''
    class Invoice:
        def subtotal(self) -> Decimal:
            return sum((price * quantity for price, quantity in self.items), Decimal("0.00"))
    '''
    response = llm.prompt("Explain how Invoice.subtotal calculates the subtotal. Use only this code:\n" + context)
    kbench.assertions.assert_contains_regex(r"(?i)price", response, expectation="The explanation identifies the unit price.")
    kbench.assertions.assert_contains_regex(r"(?i)quantity", response, expectation="The explanation identifies quantity multiplication.")


invoice_subtotal.run(kbench.llm)
