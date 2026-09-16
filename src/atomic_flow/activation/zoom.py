"""Zoom levels — how much of a node to send to the LLM."""

from __future__ import annotations

from dataclasses import dataclass

from atomic_flow.config import ActivationConfig


@dataclass
class Zoom:
    level: int          # 0..3
    est_tokens: int


def score_to_zoom(score: float, cfg: ActivationConfig) -> Zoom | None:
    """Map an activation score to a zoom level.

    Returns None if the node should be dropped.
    """
    t = cfg.zoom_thresholds
    if score >= t["zoom3"]:
        return Zoom(level=3, est_tokens=2500)
    if score >= t["zoom2"]:
        return Zoom(level=2, est_tokens=800)
    if score >= t["zoom1"]:
        return Zoom(level=1, est_tokens=30)
    if score >= t["zoom0"]:
        return Zoom(level=0, est_tokens=5)
    return None
