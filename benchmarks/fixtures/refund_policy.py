"""Refund eligibility rules used to test cross-file retrieval."""

from invoice_service import is_refundable


def refund_message(status: str, days_since_purchase: int) -> str:
    if is_refundable(status, days_since_purchase):
        return "refund approved"
    return "refund denied"
