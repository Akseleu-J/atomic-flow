import pytest
import yaml

from atomic_flow.backends.router import make_backend
from atomic_flow.config import load_config, mask_secrets, trust_project, user_dir, write_config
from atomic_flow.errors import ConfigurationError


def test_anthropic_entry_gets_anthropic_defaults(tmp_path):
    (user_dir()).mkdir(parents=True)
    (user_dir() / "config.yaml").write_text(yaml.safe_dump({"default_backend": "anthropic",
                                                            "backends": {"anthropic": {"model": "m"}}}))
    config = load_config(tmp_path)
    assert config.backends["anthropic"].base_url == "https://api.anthropic.com/v1"
    assert make_backend(config).name == "anthropic"


def test_untrusted_project_cannot_redirect_backend(tmp_path):
    (tmp_path / ".atomic_flow").mkdir()
    (tmp_path / ".atomic_flow" / "config.yaml").write_text(yaml.safe_dump(
        {"mode": "simple", "backends": {"openai-compatible": {"base_url": "https://evil.example/v1"}}}))
    config = load_config(tmp_path)
    assert config.mode == "simple" and "evil" not in config.backends["openai-compatible"].base_url
    trust_project(tmp_path)
    assert load_config(tmp_path).backends["openai-compatible"].base_url == "https://evil.example/v1"


def test_base_url_scheme_validated(tmp_path):
    with pytest.raises(ConfigurationError):
        write_config(tmp_path / "c.yaml", {"backends": {"x": {"base_url": "file:///etc/passwd"}}})
    assert not (tmp_path / "c.yaml").exists()                                # validated BEFORE writing


def test_mask_secrets_is_recursive():
    masked = mask_secrets({"backends": {"a": {"api_key": "sk-1", "api_key_env": "X", "model": "m"}}})
    assert masked["backends"]["a"] == {"api_key": "********", "api_key_env": "X", "model": "m"}


def test_server_auth_chat_hard_and_validation(project, monkeypatch):
    pytest.importorskip("fastapi")
    from conftest import ScriptedBackend
    from fastapi.testclient import TestClient

    import atomic_flow.api.server as server
    monkeypatch.setenv("ATOMIC_FLOW_SERVER_API_KEY", "k" * 32)
    monkeypatch.setenv("ATOMIC_FLOW_PROJECT_ROOT", str(project))
    backend = ScriptedBackend({"parser": {}, "refiner": {"node_ids": []}, "answerer": {"text": "hi", "confidence": "high"},
                               "critic": {"verdict": "ok", "citation_coverage": 1}})
    monkeypatch.setattr(server, "make_backend", lambda *a, **k: backend)
    client = TestClient(server.create_app())
    assert client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}]}).status_code == 401
    assert client.post("/v1/answer", json={"query": "x"}, headers={"X-API-Key": ("é" * 32).encode("latin-1")}).status_code == 401   # not a 500
    auth = {"Authorization": "Bearer " + "k" * 32}
    r = client.post("/v1/chat/completions", headers=auth, json={"model": "atomic-hard", "messages": [
        {"role": "user", "content": "old"}, {"role": "assistant", "content": "a"}, {"role": "user", "content": "refundable rule?"}]})
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "hi"   # was always 409 in v0.1
    assert client.post("/v1/answer", headers=auth, json={"query": "x", "mode": "turbo"}).status_code == 422
    assert client.get("/v1/models", headers=auth).json()["data"][0]["id"] == "atomic-simple"


def test_placeholder_server_key_refused(monkeypatch):
    pytest.importorskip("fastapi")
    import atomic_flow.api.server as server
    monkeypatch.setenv("ATOMIC_FLOW_SERVER_API_KEY", "replace-with-a-long-random-value")
    with pytest.raises(RuntimeError):
        server.create_app()
