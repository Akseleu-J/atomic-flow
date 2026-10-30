"""Backend construction from named configuration entries."""

from __future__ import annotations

from atomic_flow.backends.anthropic import AnthropicBackend
from atomic_flow.backends.base import LLMBackend
from atomic_flow.backends.kaggle import KaggleBackend
from atomic_flow.backends.openai_compatible import OpenAICompatibleBackend
from atomic_flow.config import AtomicConfig
from atomic_flow.errors import ConfigurationError
from atomic_flow.kaggle.credentials import load_credentials


def resolve_backend_name(config: AtomicConfig, name: str = "auto") -> str:
    if name != "auto":
        return name
    if config.default_backend != "auto":
        return config.default_backend
    for key, entry in config.backends.items():
        kind = entry.type or key
        if kind == "kaggle":
            credentials = load_credentials(required=False)
            if credentials and not credentials.expired:
                return key
        elif entry.resolved_api_key() or any(host in entry.base_url for host in ("localhost", "127.0.0.1")):
            return key
    raise ConfigurationError(
        "No usable backend. Set ATOMIC_FLOW_API_KEY or ANTHROPIC_API_KEY, "
        "run `atomic kaggle auth`, or configure a local backend."
    )


def make_backend(config: AtomicConfig, name: str = "auto", model: str | None = None) -> LLMBackend:
    if name == "auto" and config.routing.mode != "fixed":
        from atomic_flow.backends.routing import RoutingBackend
        if model is not None or not config.routing.routes or "auto" in config.routing.routes:
            raise ConfigurationError("routing requires named routes and no model override")
        return RoutingBackend([make_backend(config, route) for route in config.routing.routes], config.routing, config.conversation)
    resolved = resolve_backend_name(config, name)
    entry = config.backends.get(resolved)
    kind = (entry.type if entry and entry.type else None) or ("kaggle" if resolved == "kaggle" else "openai-compatible")
    if kind == "kaggle":
        backend = KaggleBackend(model=model or (entry.model if entry and entry.model else None),
                                timeout_seconds=entry.timeout_seconds if entry else 90,
                                max_output_tokens=entry.max_output_tokens if entry else None)
        if entry:
            backend.config = backend.config.model_copy(update={
                "price_in_per_mtok": entry.price_in_per_mtok, "price_out_per_mtok": entry.price_out_per_mtok,
                "max_retries": entry.max_retries,
            })
        return backend
    if entry is None:
        raise ConfigurationError(f"Backend is not configured: {resolved}")
    if model:
        entry = entry.model_copy(update={"model": model})
    if not entry.model:
        raise ConfigurationError(f"Backend {resolved!r} has no model. Set backends.{resolved}.model or pass --model.")
    if kind == "anthropic":
        return AnthropicBackend(entry, backend_name=resolved)
    return OpenAICompatibleBackend(entry, backend_name=resolved)
