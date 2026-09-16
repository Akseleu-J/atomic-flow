"""Configuration for atomic_flow.

Loads from (in order):
  1. Built-in defaults
  2. ~/.atomic_flow/config.yaml
  3. ./.atomic_flow/config.yaml (project-local)
  4. Environment variables (ATOMIC_*)
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EncoderConfig(BaseModel):
    enabled: bool = True
    model_path: Path | None = None
    embedding_dim: int = 768
    batch_size: int = 8
    max_seq_len: int = 4096
    preferred_accelerator: str = "auto"  # auto | cuda | mps | tpu | cpu


class TreeConfig(BaseModel):
    languages: list[str] = Field(default_factory=lambda: ["python"])
    ignore_patterns: list[str] = Field(
        default_factory=lambda: [
            "__pycache__/",
            ".git/",
            ".venv/",
            "venv/",
            "node_modules/",
            "dist/",
            "build/",
            "*.pyc",
        ]
    )


class ActivationConfig(BaseModel):
    top_k: int = 30
    min_score: float = 0.30
    context_budget_tokens: int = 8000
    zoom_thresholds: dict[str, float] = Field(
        default_factory=lambda: {
            "zoom3": 0.85,
            "zoom2": 0.70,
            "zoom1": 0.50,
            "zoom0": 0.30,
        }
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ATOMIC_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    encoder: EncoderConfig = Field(default_factory=EncoderConfig)
    tree: TreeConfig = Field(default_factory=TreeConfig)
    activation: ActivationConfig = Field(default_factory=ActivationConfig)

    state_dir: Path = Field(default_factory=lambda: Path.home() / ".atomic_flow")
    log_level: str = "info"

    @classmethod
    def load(cls) -> "Settings":
        """Load settings with yaml overlay."""
        data: dict = {}

        global_cfg = Path.home() / ".atomic_flow" / "config.yaml"
        if global_cfg.exists():
            data.update(yaml.safe_load(global_cfg.read_text()) or {})

        project_cfg = Path.cwd() / ".atomic_flow" / "config.yaml"
        if project_cfg.exists():
            data.update(yaml.safe_load(project_cfg.read_text()) or {})

        return cls(**data)
