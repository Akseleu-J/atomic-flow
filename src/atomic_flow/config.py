"""Layered user and project configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from atomic_flow.errors import ConfigurationError


def user_dir() -> Path:
    return Path(os.environ.get("ATOMIC_FLOW_HOME", Path.home() / ".atomic_flow")).expanduser()


def project_dir(project: str | Path | None = None) -> Path:
    return Path(project or Path.cwd()).expanduser().resolve() / ".atomic_flow"


class BackendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = "openai-compatible"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    timeout_seconds: float = Field(default=90, gt=0, le=600)
    max_retries: int = Field(default=2, ge=0, le=5)


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_limit_usd: float = Field(default=10.0, ge=0)


class AtomicConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = "medium"
    context_budget_tokens: int = Field(default=8000, ge=256, le=200_000)
    max_attempts: int = Field(default=3, ge=1, le=10)
    auto_mode: bool = True
    default_backend: str = "kaggle"
    tree: dict[str, Any] = Field(default_factory=lambda: {"ignore": [".git", ".venv", "venv", "node_modules", "__pycache__", ".atomic_flow"]})
    backends: dict[str, BackendConfig] = Field(default_factory=lambda: {
        "openai-compatible": BackendConfig(),
        "kaggle": BackendConfig(name="kaggle", base_url="", api_key="", model=""),
    })
    budget: BudgetConfig = Field(default_factory=BudgetConfig)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Cannot read configuration {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigurationError(f"Configuration at {path} must contain a YAML mapping.")
    return loaded


def load_config(project: str | Path | None = None) -> AtomicConfig:
    """Read defaults, then user config, project config, and environment overrides."""
    merged = AtomicConfig().model_dump(mode="python")
    for path in (user_dir() / "config.yaml", project_dir(project) / "config.yaml"):
        _deep_update(merged, _read_yaml(path))
    backend = merged.setdefault("backends", {}).setdefault("openai-compatible", {})
    backend["base_url"] = os.getenv("ATOMIC_FLOW_BASE_URL", backend.get("base_url", "https://api.openai.com/v1"))
    backend["api_key"] = os.getenv("ATOMIC_FLOW_API_KEY", backend.get("api_key", ""))
    backend["model"] = os.getenv("ATOMIC_FLOW_MODEL", backend.get("model", "gpt-4o-mini"))
    try:
        return AtomicConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid atomic_flow configuration:\n{exc}") from exc


def _deep_update(target: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def write_config(path: Path, values: dict[str, Any], *, merge: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _read_yaml(path) if merge else {}
    _deep_update(current, values)
    path.write_text(yaml.safe_dump(current, sort_keys=False, allow_unicode=True), encoding="utf-8")
