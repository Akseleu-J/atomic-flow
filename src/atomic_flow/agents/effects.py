"""Host-owned unresolved effect latch, independent of prompt/history state."""
from __future__ import annotations

import threading
import uuid

from atomic_flow.errors import AtomicFlowError


class UnresolvedEffects(AtomicFlowError):
    """External effects need host review; neither model retry nor replan is safe."""


class EffectState:
    def __init__(self):
        self._lock = threading.RLock()
        self._unknown = None

    @property
    def blocked(self) -> bool:
        with self._lock:
            return self._unknown is not None

    def mark(self, tool: str) -> dict | None:
        """Latch before persistence. Store no arguments, source or exception text."""
        with self._lock:
            if self._unknown is not None:
                return None
            self._unknown = {"effect_id": "effect_" + uuid.uuid4().hex, "tool": tool,
                             "side_effects_may_have_occurred": True}
            return dict(self._unknown)

    def observation(self) -> dict | None:
        with self._lock:
            return dict(self._unknown) if self._unknown is not None else None

    def require_clear(self) -> None:
        if self.blocked:
            raise UnresolvedEffects("unresolved tool effects; inspect external state before authorizing new work (no automatic replay)")
