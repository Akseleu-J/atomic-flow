"""VAT lookup per country (distractor module for the benchmark)."""

VAT_RATES = {"DE": 0.19, "FR": 0.20, "KZ": 0.12}


def vat_rate(country_code: str) -> float:
    """Return the VAT rate for a country; unknown countries are charged no VAT."""
    return VAT_RATES.get(country_code.upper(), 0.0)
