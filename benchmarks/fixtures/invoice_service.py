"""Small deterministic fixture used by the atomic_flow benchmark suite."""

from decimal import Decimal


class Invoice:
    def __init__(self, items: list[tuple[Decimal, int]], discount_percent: int = 0):
        self.items = items
        self.discount_percent = discount_percent

    def subtotal(self) -> Decimal:
        """Return the sum of each item's unit price multiplied by its quantity."""
        return sum((price * quantity for price, quantity in self.items), Decimal("0.00"))

    def total(self) -> Decimal:
        """Apply the percentage discount to the subtotal and round to cents."""
        discounted = self.subtotal() * (Decimal("100") - Decimal(self.discount_percent)) / Decimal("100")
        return discounted.quantize(Decimal("0.01"))


def is_refundable(status: str, days_since_purchase: int) -> bool:
    """Refunds are allowed for paid orders up to 30 days after purchase."""
    return status == "paid" and 0 <= days_since_purchase <= 30
