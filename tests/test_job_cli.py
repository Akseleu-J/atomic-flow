import json
import time

from atomic_flow.cli import main
from atomic_flow.sdk import jobs


def test_cli_and_sdk_share_durable_job_without_model_backend(tmp_path, monkeypatch, capsys):
    project = tmp_path / "project"
    project.mkdir()
    (project / "probe.py").write_text("print('cli-job-ok')\n")
    monkeypatch.setattr("atomic_flow.cli.make_backend", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model backend")))
    assert main(["job", "start", "--project", str(project), "--mode", "local", "--", "probe.py"]) == 0
    ident = json.loads(capsys.readouterr().out)["id"]
    store = jobs(project)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status = store.status(ident)
        if status["status"] in {"succeeded", "failed", "interrupted"}:
            break
        time.sleep(0.1)
    assert status["status"] == "succeeded", status
    assert main(["job", "output", "--project", str(project), ident, "--limit", "65536"]) == 0
    page = json.loads(capsys.readouterr().out)
    assert "cli-job-ok" in page["text"] and page["output_complete"]
    assert main(["job", "reconcile", "--project", str(project), ident]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert (project / "probe.py").read_text() == "print('cli-job-ok')\n"
