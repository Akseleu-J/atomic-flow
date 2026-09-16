"""Encoder — abstract + concrete implementations.

v0.0 provides two encoders:
  * ``AtomicCoreEncoder`` — wraps atomic-core (gated deltanet2, 250M).
    Loaded only if torch + model file are available.
  * ``BM25FallbackEncoder`` — pure-Python fallback using token frequency.
    Used when atomic-core isn't available.

Both expose the same interface: ``encode(texts) -> np.ndarray [N, D]``.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from atomic_flow.config import EncoderConfig

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\sA-Za-z0-9_]")


class Encoder(ABC):
    """Abstract encoder interface."""

    dim: int
    name: str

    @abstractmethod
    def encode(self, texts: list[str]) -> np.ndarray:
        """Return [N, dim] float32 matrix."""

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


# ---------------------------------------------------------------------------
# Fallback: BM25-style token-hash embedding
# ---------------------------------------------------------------------------


class BM25FallbackEncoder(Encoder):
    """A cheap deterministic fallback. Not semantic, but stable.

    Tokenizes text, hashes each token into a fixed-dim vector with
    log-frequency weighting. Works for smoke-testing the pipeline
    without torch or a real model.
    """

    name = "bm25-fallback"

    def __init__(self, dim: int = 768):
        self.dim = dim

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            counts: dict[str, int] = {}
            for tok in _TOKEN_RE.findall(text):
                counts[tok] = counts.get(tok, 0) + 1
            for tok, cnt in counts.items():
                idx = hash(tok) % self.dim
                out[i, idx] += 1.0 + np.log1p(cnt)
            norm = np.linalg.norm(out[i])
            if norm > 0:
                out[i] /= norm
        return out


# ---------------------------------------------------------------------------
# Real: atomic-core wrapper
# ---------------------------------------------------------------------------


class AtomicCoreEncoder(Encoder):
    """Wraps the gated deltanet2 encoder.

    Placeholder — the exact loading logic depends on how you exported
    atomic-core. Two common paths:

      (a) HuggingFace-compatible: AutoModel + AutoTokenizer.
      (b) Custom checkpoint: torch.load + your model class.

    Fill in ``_load_model`` and ``_forward`` once the export format is
    decided. Until then this class raises NotImplementedError and the
    factory falls back to BM25.
    """

    name = "atomic-core"

    def __init__(self, cfg: EncoderConfig):
        self.cfg = cfg
        self.dim = cfg.embedding_dim
        self._model = None
        self._tokenizer = None
        self._device = self._detect_device(cfg.preferred_accelerator)
        self._load_model()

    def _detect_device(self, preferred: str) -> str:
        try:
            import torch
        except ImportError:
            return "cpu"

        if preferred == "cuda" and torch.cuda.is_available():
            return "cuda"
        if preferred == "mps" and torch.backends.mps.is_available():
            return "mps"
        if preferred == "cpu":
            return "cpu"

        # auto
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def _load_model(self) -> None:
        if self.cfg.model_path is None:
            raise NotImplementedError(
                "atomic-core: model_path not configured. "
                "Set encoder.model_path in config.yaml."
            )
        if not Path(self.cfg.model_path).exists():
            raise FileNotFoundError(
                f"atomic-core model not found: {self.cfg.model_path}"
            )

        # TODO: implement once export format is fixed.
        # Example (HuggingFace path):
        #   from transformers import AutoModel, AutoTokenizer
        #   self._tokenizer = AutoTokenizer.from_pretrained(self.cfg.model_path)
        #   self._model = AutoModel.from_pretrained(self.cfg.model_path).to(self._device)
        #   self._model.eval()
        raise NotImplementedError(
            "AtomicCoreEncoder.load_model: not implemented yet. "
            "See core/encoder.py for integration points."
        )

    def encode(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_encoder(cfg: EncoderConfig | None = None) -> Encoder:
    """Return the best available encoder.

    Tries atomic-core; falls back to BM25 if unavailable.
    """
    cfg = cfg or EncoderConfig()

    if not cfg.enabled:
        logger.info("Encoder disabled by config — using BM25 fallback")
        return BM25FallbackEncoder(dim=cfg.embedding_dim)

    try:
        return AtomicCoreEncoder(cfg)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "atomic-core unavailable (%s) — falling back to BM25", e
        )
        return BM25FallbackEncoder(dim=cfg.embedding_dim)
