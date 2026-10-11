"""Kaggle Model Proxy backend with short-lived key and model allowlist checks."""

from __future__ import annotations

from atomic_flow.backends.openai_compatible import ChatResult, OpenAICompatibleBackend
from atomic_flow.config import BackendConfig
from atomic_flow.errors import ConfigurationError
from atomic_flow.kaggle.credentials import KaggleCredentials, load_credentials


def normalize_model(model: str) -> str:
    return model.rsplit("/", 1)[-1].replace("@", "-").casefold()


class KaggleBackend(OpenAICompatibleBackend):
    def __init__(self, *, model: str | None = None, credentials: KaggleCredentials | None = None, timeout_seconds: float = 90):
        self.credentials = credentials or load_credentials()
        if self.credentials.expired:
            raise ConfigurationError("Kaggle Model Proxy key has expired. Run `atomic kaggle auth` to refresh it.")
        chosen_model = model or self.credentials.default_model
        if not chosen_model:
            raise ConfigurationError("No Kaggle model selected. Run `atomic kaggle models` and set LLM_DEFAULT or pass --model.")
        if self.credentials.allowed_models and normalize_model(chosen_model) not in {normalize_model(item) for item in self.credentials.allowed_models}:
            raise ConfigurationError(f"Model {chosen_model!r} is not allowed by the current Kaggle proxy token. Run `atomic kaggle models` or refresh credentials.")
        self.credentials = self.credentials
        super().__init__(BackendConfig(name="kaggle", base_url=self.credentials.url,
                                       api_key=self.credentials.api_key, model=chosen_model,
                                       timeout_seconds=timeout_seconds), backend_name="kaggle")

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, model: str | None = None) -> ChatResult:
        if self.credentials.expired:
            raise ConfigurationError("Kaggle Model Proxy key expired during this process. Run `atomic kaggle auth`.")
        return super().chat(messages, json_mode=json_mode, model=model)
