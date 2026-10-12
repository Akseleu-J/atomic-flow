"""Backend construction from named configuration entries."""

from __future__ import annotations

from atomic_flow.backends.anthropic import AnthropicBackend
from atomic_flow.backends.base import LLMBackend
from atomic_flow.backends.kaggle import KaggleBackend
from atomic_flow.backends.openai_compatible import OpenAICompatibleBackend
from atomic_flow.config import AtomicConfig
from atomic_flow.errors import ConfigurationError


def resolve_backend_name(config: AtomicConfig, name: str = "auto") -> str:
    if name != "auto":
        return name
    return "openai-compatible" if config.default_backend == "auto" else config.default_backend


def make_backend(config: AtomicConfig, name: str = "auto", model: str | None = None) -> LLMBackend:
    resolved = resolve_backend_name(config, name)
    entry = config.backends.get(resolved)
    kind = (entry.type if entry and entry.type else None) or ("kaggle" if resolved == "kaggle" else "openai-compatible")
    if kind == "kaggle":
        return KaggleBackend(model=model or (entry.model if entry and entry.model else None),
                             timeout_seconds=entry.timeout_seconds if entry else 90)
    if entry is None:
        raise ConfigurationError(f"Backend is not configured: {resolved}")
    if model:
        entry = entry.model_copy(update={"model": model})
    if not entry.model:
        raise ConfigurationError(f"Backend {resolved!r} has no model. Set backends.{resolved}.model or pass --model.")
    if kind == "anthropic":
        return AnthropicBackend(entry, backend_name=resolved)
    return OpenAICompatibleBackend(entry, backend_name=resolved)
