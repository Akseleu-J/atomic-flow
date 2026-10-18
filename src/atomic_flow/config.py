"""Layered user and project configuration (with a trust gate for project-level files)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from atomic_flow.errors import ConfigurationError
from atomic_flow.tree.defaults import DEFAULT_IGNORE

log = logging.getLogger(__name__)

BackendType = Literal["openai-compatible", "anthropic", "kaggle"]

# Per-provider defaults. v0.1 gave EVERY backend the OpenAI URL/model, so an `anthropic:` block
# without base_url silently sent requests to api.openai.com/v1/messages.
BACKEND_DEFAULTS: dict[str, dict[str, Any]] = {
    "openai-compatible": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "api_key_env": "ATOMIC_FLOW_API_KEY"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1", "model": "", "api_key_env": "ANTHROPIC_API_KEY"},
    "kaggle": {"base_url": "", "model": "", "api_key_env": ""},
}

# A cloned repository must not be able to redirect your API key or source code.
UNTRUSTED_PROJECT_KEYS = ("default_backend", "backends", "budget", "artifacts", "execution", "task")


def user_dir() -> Path:
    return Path(os.environ.get("ATOMIC_FLOW_HOME", Path.home() / ".atomic_flow")).expanduser()


def project_dir(project: str | Path | None = None) -> Path:
    return Path(project or Path.cwd()).expanduser().resolve() / ".atomic_flow"


class BackendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: BackendType | None = None
    name: str = "openai-compatible"
    base_url: str = ""
    api_key: str = ""
    api_key_env: str = ""
    model: str = ""
    timeout_seconds: float = Field(default=90, gt=0, le=600)
    max_retries: int = Field(default=2, ge=0, le=5)
    temperature: float | None = Field(default=0.1, ge=0, le=2)   # null -> omit (some reasoning models reject it)
    max_output_tokens: int | None = Field(default=None, ge=16, le=200_000)
    price_in_per_mtok: float | None = Field(default=None, ge=0)  # lets budget enforcement work without provider cost
    price_out_per_mtok: float | None = Field(default=None, ge=0)

    @field_validator("base_url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        if value and urlparse(value).scheme not in {"http", "https"}:
            raise ValueError("base_url must start with http:// or https:// (urllib would happily open file:// URLs)")
        return value

    def resolved_api_key(self) -> str:
        return self.api_key or (os.getenv(self.api_key_env, "") if self.api_key_env else "")


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_limit_usd: float = Field(default=10.0, ge=0)          # 0 disables; enforced locally in budget.py


class ArtifactsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    save: bool = True                                           # run artifacts contain source snippets: opt out here
    keep_days: int = Field(default=30, ge=1)


class TreeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ignore: list[str] = Field(default_factory=lambda: list(DEFAULT_IGNORE))
    respect_gitignore: bool = True
    auto_refresh: bool = True                                   # re-index changed files before every question


class TaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_replans: int = Field(default=2, ge=0, le=5)
    max_budget_usd: float = Field(default=2.0, ge=0)
    fail_on_budget_exceeded: bool = True
    max_model_calls: int = Field(default=100, ge=1, le=1000)
    default_max_output_tokens: int = Field(default=4096, ge=16, le=200_000)


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    mode: Literal["docker", "local"] = Field(default_factory=lambda: os.getenv("ATOMIC_FLOW_EXECUTION_MODE", "docker"))
    image: str = "atomic-flow-checks:local"
    output_bytes: int = Field(default=16384, ge=1024, le=1_000_000)


class AtomicConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["simple", "medium", "hard"] = "medium"
    context_budget_tokens: int = Field(default=8000, ge=256, le=200_000)
    max_attempts: int = Field(default=3, ge=1, le=10)
    auto_mode: bool = True
    default_backend: str = "auto"
    tree: TreeConfig = Field(default_factory=TreeConfig)
    backends: dict[str, BackendConfig] = Field(default_factory=lambda: {
        "openai-compatible": BackendConfig(type="openai-compatible"),
        "kaggle": BackendConfig(type="kaggle", name="kaggle"),
    })
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    artifacts: ArtifactsConfig = Field(default_factory=ArtifactsConfig)
    task: TaskConfig = Field(default_factory=TaskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)


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


def _deep_update(target: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


# ------------------------------------------------------------------ project trust

def _trust_file() -> Path:
    return user_dir() / "trusted_projects.txt"


def is_trusted(project: str | Path) -> bool:
    target = str(Path(project).expanduser().resolve())
    try:
        return target in {line.strip() for line in _trust_file().read_text(encoding="utf-8").splitlines()}
    except OSError:
        return False


def trust_project(project: str | Path) -> None:
    target = str(Path(project).expanduser().resolve())
    if not is_trusted(target):
        _trust_file().parent.mkdir(parents=True, exist_ok=True)
        with _trust_file().open("a", encoding="utf-8") as handle:
            handle.write(target + "\n")


def load_config(project: str | Path | None = None) -> AtomicConfig:
    """defaults < user config < project config (trust-gated) < environment."""
    merged = AtomicConfig().model_dump(mode="python")
    _deep_update(merged, _read_yaml(user_dir() / "config.yaml"))
    project_yaml = _read_yaml(project_dir(project) / "config.yaml")
    root = Path(project or Path.cwd()).expanduser().resolve()
    if project_yaml and not is_trusted(root):
        dropped = [key for key in UNTRUSTED_PROJECT_KEYS if key in project_yaml]
        if dropped:
            log.warning("Ignoring %s from untrusted %s/.atomic_flow/config.yaml. Review it, then run `atomic trust`.",
                        ", ".join(dropped), root)
        project_yaml = {k: v for k, v in project_yaml.items() if k not in UNTRUSTED_PROJECT_KEYS}
    _deep_update(merged, project_yaml)
    if "ATOMIC_FLOW_EXECUTION_MODE" in os.environ:
        merged.setdefault("execution", {})["mode"] = os.environ["ATOMIC_FLOW_EXECUTION_MODE"]

    backends = merged.setdefault("backends", {})
    for key, entry in backends.items():                                   # fill provider defaults per entry
        kind = entry.get("type") or (key if key in BACKEND_DEFAULTS else "openai-compatible")
        entry["type"] = kind
        for field_name, default in BACKEND_DEFAULTS[kind].items():
            if not entry.get(field_name):
                entry[field_name] = default
    openai = backends.setdefault("openai-compatible", {"type": "openai-compatible"})
    openai["base_url"] = os.getenv("ATOMIC_FLOW_BASE_URL", openai.get("base_url") or BACKEND_DEFAULTS["openai-compatible"]["base_url"])
    openai["api_key"] = os.getenv("ATOMIC_FLOW_API_KEY", openai.get("api_key", ""))
    openai["model"] = os.getenv("ATOMIC_FLOW_MODEL", openai.get("model") or BACKEND_DEFAULTS["openai-compatible"]["model"])
    try:
        return AtomicConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid atomic_flow configuration:\n{exc}") from exc


def mask_secrets(value: Any) -> Any:
    """Recursively hide api keys (v0.1 only masked a key literally ending in `api_key`)."""
    if isinstance(value, dict):
        return {k: ("********" if "api_key" in str(k) and v and not str(k).endswith("_env") else mask_secrets(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [mask_secrets(v) for v in value]
    return value


def write_config(path: Path, values: dict[str, Any], *, merge: bool = True, validate: bool = True) -> None:
    """Validate BEFORE touching the file, write atomically, keep it private (it may hold an api_key)."""
    current = _read_yaml(path) if merge else {}
    _deep_update(current, values)
    if validate:
        probe = AtomicConfig().model_dump(mode="python")
        _deep_update(probe, current)
        try:
            AtomicConfig.model_validate(probe)
        except ValidationError as exc:
            raise ConfigurationError(f"Refusing to write an invalid configuration:\n{exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(current, sort_keys=False, allow_unicode=True), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)
