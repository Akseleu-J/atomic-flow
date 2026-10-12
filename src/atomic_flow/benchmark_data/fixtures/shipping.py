"""Shipping price rules (distractor module for the benchmark)."""

FREE_SHIPPING_THRESHOLD = 50
FLAT_RATE = 4.99


def shipping_cost(order_total: float, express: bool = False) -> float:
    """Orders of 50 or more ship free; otherwise a flat rate, doubled for express delivery."""
    if order_total >= FREE_SHIPPING_THRESHOLD:
        return 0.0
    return FLAT_RATE * (2 if express else 1)
