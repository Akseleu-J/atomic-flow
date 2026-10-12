import json

from atomic_flow.cli import main


def test_cli_tree_refresh_status_and_retrieval(project, capsys):
    assert main(["tree", "status", "--path", str(project)]) == 0
    (project / "shipping.py").write_text((project / "shipping.py").read_text() + "\nEXTRA = 1\n")
    assert main(["tree", "status", "--path", str(project)]) == 1          # body edit is now visible
    assert main(["tree", "refresh", "--path", str(project)]) == 0
    assert main(["benchmark", "retrieval"]) == 0


def test_cli_ask_dry_run_and_config_masking(project, capsys):
    assert main(["ask", "refundable rule", "--project", str(project), "--mode", "medium", "--dry-run", "--explain"]) == 0
    out = capsys.readouterr().out
    assert "is_refundable" in out and "PROJECT SKELETON" in out
    assert main(["config", "set", "backends.openai-compatible.api_key", "sk-secret"]) == 0
    capsys.readouterr()
    assert main(["config", "get", "backends"]) == 0
    assert "sk-secret" not in capsys.readouterr().out
    assert main(["config", "set", "mode", "turbo"]) == 2                  # rejected before it is written


def test_cli_ask_json_with_fake_backend(project, monkeypatch, capsys):
    from conftest import ScriptedBackend

    import atomic_flow.cli as cli
    monkeypatch.setattr(cli, "make_backend", lambda *a, **k: ScriptedBackend({"answerer": {"text": "`is_refundable`", "confidence": "high"}}))
    assert main(["ask", "refundable?", "--project", str(project), "--mode", "simple", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["mode_used"] == "simple"
