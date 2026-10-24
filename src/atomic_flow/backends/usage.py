"""Normalize provider usage independently of generated content parsing."""
from __future__ import annotations

import math
from dataclasses import dataclass

from atomic_flow.backends.base import estimate_cost, optional_token_count


@dataclass(frozen=True)
class AnthropicCachePrices:
    """Explicit host tariffs, USD/MTok; no inferred model multipliers or TTL."""

    read_per_mtok: float | None = None
    write_5m_per_mtok: float | None = None
    write_1h_per_mtok: float | None = None

    def __post_init__(self):
        for value in (self.read_per_mtok, self.write_5m_per_mtok, self.write_1h_per_mtok):
            if value is not None:
                _amount(value)


def _amount(value):
    # A provider cost must be a JSON number; strings, booleans and None are malformed.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("cost must be a JSON number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("invalid cost")
    return result


def openai_usage(payload, config):
    usage = payload.get("usage", {})
    if not isinstance(usage, dict):
        return 0, 0, None, "invalid_provider_usage", None
    raw_in = usage.get("prompt_tokens", usage.get("input_tokens"))
    raw_out = usage.get("completion_tokens", usage.get("output_tokens"))
    incoming, outgoing = optional_token_count(raw_in), optional_token_count(raw_out)
    cache = usage.get("prompt_tokens_details") or {}
    cached = optional_token_count(cache.get("cached_tokens")) if isinstance(cache, dict) else None
    cost, source = None, "unknown"
    try:
        if "cost_usd" in usage or "total_cost_usd" in usage:
            cost = _amount(usage.get("cost_usd", usage.get("total_cost_usd")))
            source = "provider"
        elif "cost" in usage and not isinstance(usage["cost"], dict):
            # OpenRouter: scalar USD cost, including an explicit numeric zero.
            cost = _amount(usage["cost"])
            source = "provider"
        else:
            nested = usage.get("cost") or {}
            fields = ("input_tokens_cost_nanodollars", "output_tokens_cost_nanodollars")
            if any(k in nested or k in usage for k in fields):
                # A partial monetary record is not a complete known price.
                if not all(k in nested or k in usage for k in fields):
                    raise ValueError("partial provider cost")
                cost = sum(_amount(nested.get(k, usage.get(k))) for k in fields) / 1e9
                source = "provider"
            elif incoming is not None and outgoing is not None or config.price_in_per_mtok == config.price_out_per_mtok == 0:
                cost = estimate_cost(config, incoming or 0, outgoing or 0)
                if cost is not None:
                    cost = _amount(cost)
                    source = "configured_estimate"
    except (TypeError, ValueError, OverflowError):
        # Malformed provider cost cannot be overridden with a configured zero.
        return incoming or 0, outgoing or 0, None, "invalid_provider_usage", cached
    return incoming or 0, outgoing or 0, cost, source, cached


def anthropic_usage(payload, config, cache_prices=None, *, model=None):
    """Normalize documented Messages counters before any content parsing.

    input_tokens excludes cache reads and writes. Cache write TTLs have separate
    counters/rates; tariff multiplication is deliberately never guessed. Prices
    are host estimates, not provider invoices; cache/server-tool/context-tier
    pricing must be explicitly supported before cost can be considered known.
    """
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0, 0, None, "invalid_provider_usage", None, None
    incoming = optional_token_count(usage.get("input_tokens"))
    outgoing = optional_token_count(usage.get("output_tokens"))
    read = optional_token_count(usage.get("cache_read_input_tokens", 0))
    write = optional_token_count(usage.get("cache_creation_input_tokens", 0))
    total = (incoming or 0) + (read or 0) + (write or 0)
    if None in (incoming, outgoing, read, write):
        return total, outgoing or 0, None, "invalid_provider_usage", read, write
    if model is not None and model != config.model:
        return total, outgoing, None, "model_price_mismatch", read, write
    # Server-side tools incur additional charges; a text-only adapter cannot
    # price them from token rates. Explicit zeros are harmless, positive/unknown
    # counters are not silently dropped from a supposedly complete estimate.
    server = usage.get("server_tool_use", {})
    if not isinstance(server, dict) or any(type(value) is not int or value != 0 for value in server.values()):
        return total, outgoing, None, "unpriced_server_tool_usage", read, write
    try:
        base = estimate_cost(config, incoming, outgoing)
        if base is None:
            return total, outgoing, None, "unknown", read, write
        cost = _amount(base)
        if read:
            if cache_prices is None or cache_prices.read_per_mtok is None:
                return total, outgoing, None, "unpriced_cache_usage", read, write
            cost += read * _amount(cache_prices.read_per_mtok) / 1e6
        if write:
            breakdown = usage.get("cache_creation")
            if not isinstance(breakdown, dict):
                return total, outgoing, None, "unpriced_cache_usage", read, write
            five = optional_token_count(breakdown.get("ephemeral_5m_input_tokens"))
            hour = optional_token_count(breakdown.get("ephemeral_1h_input_tokens"))
            if five is None or hour is None or five + hour != write:
                return total, outgoing, None, "invalid_provider_usage", read, write
            for count, rate in ((five, getattr(cache_prices, "write_5m_per_mtok", None)),
                                (hour, getattr(cache_prices, "write_1h_per_mtok", None))):
                if count:
                    if rate is None:
                        return total, outgoing, None, "unpriced_cache_usage", read, write
                    cost += count * _amount(rate) / 1e6
        return total, outgoing, _amount(cost), "configured_estimate", read, write
    except (TypeError, ValueError, OverflowError):
        return total, outgoing, None, "invalid_provider_usage", read, write
