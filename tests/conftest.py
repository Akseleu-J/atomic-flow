import json
import re
import shutil
from pathlib import Path

import pytest

from atomic_flow.backends.base import ChatResult
from atomic_flow.benchmarking import benchmark_root
from atomic_flow.config import AtomicConfig, BackendConfig
from atomic_flow.tree.builder import build_tree, save_tree


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ATOMIC_FLOW_EXECUTION_MODE", "local")
    monkeypatch.setenv("ATOMIC_FLOW_HOME", str(tmp_path / "home"))
    for var in ("ATOMIC_FLOW_BASE_URL", "ATOMIC_FLOW_API_KEY", "ATOMIC_FLOW_MODEL", "ATOMIC_FLOW_SERVER_API_KEY",
                "ATOMIC_FLOW_PROJECT_ROOT", "ATOMIC_FLOW_REQUIRE_API_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def project(tmp_path) -> Path:
    root = tmp_path / "proj"
    shutil.copytree(benchmark_root() / "fixtures", root)
    save_tree(root, build_tree(root))
    return root


@pytest.fixture
def config() -> AtomicConfig:
    return AtomicConfig()


class ScriptedBackend:
    """Returns canned JSON per role. A handler is a dict, a raw string, a callable(user_text)->dict|str,
    or a list consumed one item per call."""

    name = "fake"

    def __init__(self, handlers: dict):
        self.handlers = {k: (list(v) if isinstance(v, list) else v) for k, v in handlers.items()}
        self.config = BackendConfig(model="fake-model", price_in_per_mtok=1, price_out_per_mtok=1)
        self.calls: list[tuple[str, list[dict]]] = []

    def chat(self, messages, *, json_mode=False, model=None) -> ChatResult:
        user = messages[1]["content"]
        role = re.search(r"<role>(\w+)</role>", user).group(1)
        self.calls.append((role, messages))
        handler = self.handlers[role]
        if isinstance(handler, list):
            handler = handler.pop(0) if len(handler) > 1 else handler[0]
        if callable(handler):
            handler = handler(user)
        content = handler if isinstance(handler, str) else json.dumps(handler)
        return ChatResult(content, 100, 50, 0.001, 5)

    def roles(self):
        return [r for r, _ in self.calls]


def alias_of(user_text: str, label: str) -> str:
    """Find the short id the context assigned to `label` (e.g. 'invoice_service.py::is_refundable')."""
    match = re.search(r"NODE (n\d+)\n" + re.escape(label) + r" \[", user_text)
    assert match, f"{label} not in context"
    return match.group(1)
