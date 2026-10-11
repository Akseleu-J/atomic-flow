"""Backend construction from named configuration entries."""

from atomic_flow.backends.anthropic import AnthropicBackend
from atomic_flow.backends.kaggle import KaggleBackend
from atomic_flow.backends.openai_compatible import OpenAICompatibleBackend
from atomic_flow.config import AtomicConfig
from atomic_flow.errors import ConfigurationError


def make_backend(config: AtomicConfig, name: str = "auto", model: str | None = None):
    resolved = (config.default_backend if config.default_backend != "auto" else "openai-compatible") if name == "auto" else name
    if resolved == "kaggle":
        backend_config = config.backends.get("kaggle")
        return KaggleBackend(model=model or (backend_config.model if backend_config and backend_config.model else None),
                             timeout_seconds=backend_config.timeout_seconds if backend_config else 90)
    if resolved not in config.backends:
        raise ConfigurationError(f"Backend is not configured: {resolved}")
    backend_config = config.backends[resolved]
    if model:
        backend_config = backend_config.model_copy(update={"model": model})
    if resolved == "anthropic":
        return AnthropicBackend(backend_config)
    return OpenAICompatibleBackend(backend_config, backend_name=resolved)
