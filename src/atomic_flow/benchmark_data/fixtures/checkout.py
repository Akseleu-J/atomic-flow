"""Checkout composes invoice, shipping and tax (multi-file composition case)."""

from invoice_service import Invoice
from shipping import shipping_cost
from tax_rules import vat_rate


def checkout_total(invoice: Invoice, country_code: str, express: bool = False) -> float:
    """Invoice total plus VAT for the country plus shipping computed from the invoice total."""
    net = float(invoice.total())
    with_vat = net * (1 + vat_rate(country_code))
    return round(with_vat + shipping_cost(net, express), 2)
