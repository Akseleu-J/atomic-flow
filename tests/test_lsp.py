import json
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from atomic_flow.agents.source import SourceConflict
from atomic_flow.lsp import LSPClient, LSPError, _offset, recover_lsp_edit

SERVER = Path(__file__).parent / "fixtures" / "lsp_server.py"


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("a = '😀'\n", encoding="utf-8")
    (root / "b.py").write_text("b = 2\n", encoding="utf-8")
    monkeypatch.setenv("ATOMIC_FLOW_HOME", str(tmp_path / "state"))
    return root


def client(project, mode="normal", **kwargs):
    return LSPClient(project, [sys.executable, str(SERVER.resolve()), mode], approved=True, **kwargs)


def test_command_requires_explicit_host_authority(project):
    with pytest.raises(LSPError, match="approval"):
        LSPClient(project, [sys.executable])
    with pytest.raises(LSPError, match="absolute"):
        LSPClient(project, ["python"], approved=True)


@pytest.mark.parametrize("mode", ["normal", "fragmented", "unsolicited"])
def test_definitions_references_diagnostics_and_shutdown(project, mode):
    with client(project, mode) as peer:
        doc = peer.read_document("a.py")
        assert doc["version"] == 1
        definitions = peer.definition("a.py", 0, 0)
        assert definitions[0]["path"] == "a.py"
        assert definitions[0]["content_hash"] == doc["content_hash"]
        assert peer.references("a.py", 0, 0) == definitions
        diagnostic = peer.diagnostics("a.py")
        assert diagnostic["version"] == 1
        assert diagnostic["diagnostics"][0]["message"] == "synthetic diagnostic"
        diagnostic["diagnostics"].clear()
        assert peer.diagnostics("a.py")["diagnostics"]
        proc = peer._process
    assert proc.poll() is not None
    assert (project / "a.py").read_text(encoding="utf-8") == "a = '😀'\n"


def test_sync_versions_and_stale_diagnostics(project):
    with client(project, "stale-diagnostics") as peer:
        peer.read_document("a.py")
        peer.definition("a.py", 0, 0)
        assert peer.diagnostics("a.py")["available"] is False
        (project / "a.py").write_text("z = 3\n", encoding="utf-8")
        assert peer.read_document("a.py")["version"] == 2
        peer.references("a.py", 0, 0)
        assert peer.diagnostics("a.py")["available"] is False


@pytest.mark.parametrize("mode", ["oversized", "partial", "bad-header", "utf8"])
def test_frame_bounds_and_negotiation(project, mode):
    with pytest.raises(LSPError):
        client(project, mode, timeout=0.4).start()


@pytest.mark.parametrize("mode", ["outside", "private"])
def test_location_confinement(project, mode):
    with client(project, mode) as peer, pytest.raises(LSPError):
        peer.definition("a.py", 0, 0)


def test_clean_child_environment(project, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "do-not-forward")
    monkeypatch.setenv("PYTHONPATH", "do-not-forward")
    with client(project, "environment"):
        state = json.loads((project / "child-env.json").read_text())
        assert state["secret"] is None
        assert state["pythonpath"] is None
        assert Path(state["home"]) != Path.home()
        home = Path(state["home"])
    assert not home.exists()


def test_timeout_and_cancellation_are_bounded(project):
    with client(project, "timeout") as peer:
        peer.timeout = 0.3
        start = time.monotonic()
        with pytest.raises(LSPError, match="timed out"):
            peer.definition("a.py", 0, 0)
        assert time.monotonic() - start < 3
        assert peer._process.poll() is not None
    cancelled = threading.Event()

    def check():
        if cancelled.is_set():
            raise InterruptedError("host cancelled")

    with client(project, "timeout", cancel_check=check) as peer:
        timer = threading.Timer(0.1, cancelled.set)
        timer.start()
        with pytest.raises(InterruptedError):
            peer.definition("a.py", 0, 0)
        timer.join()
        assert peer._process.poll() is not None


def test_server_errors_do_not_leak(project):
    with client(project, "server-error") as peer, pytest.raises(LSPError) as error:
        peer.definition("a.py", 0, 0)
    assert "private-secret" not in str(error.value)


def test_utf16_offsets_crlf_non_bmp_and_bounds():
    text = "a😀b\r\nx\n"
    assert _offset(text, {"line": 0, "character": 3}) == 2
    assert _offset(text, {"line": 1, "character": 0}) == 5
    assert _offset(text, {"line": 2, "character": 0}) == len(text)
    for position in [{"line": 0, "character": 2}, {"line": 0, "character": 5}, {"line": 9, "character": 0},
                     {"line": True, "character": 0}]:
        with pytest.raises(LSPError):
            _offset(text, position)
    assert _offset("a\u2028b", {"line": 0, "character": 2}) == 2


def test_utf16_rename_non_bmp_and_half_surrogate_rejection(project):
    with client(project, "nonbmp") as peer:
        peer.read_document("a.py")
        preview = peer.preview_rename("a.py", 0, 5, "x")
        peer.apply_preview(preview, approved=True, expected_digest=preview.digest)
        assert (project / "a.py").read_text(encoding="utf-8") == "a = 'x'\n"
    (project / "a.py").write_text("a = '😀'\n", encoding="utf-8")
    with client(project, "half-surrogate") as peer:
        peer.read_document("a.py")
        with pytest.raises(LSPError, match="surrogate"):
            peer.preview_rename("a.py", 0, 5, "x")


@pytest.mark.parametrize("mode", ["normal", "unversioned"])
def test_checked_multi_file_preview_and_apply(project, mode):
    with client(project, mode) as peer:
        peer.read_document("a.py")
        peer.read_document("b.py")
        preview = peer.preview_rename("a.py", 0, 0, "renamed")
        assert len(preview.changes) == 2
        assert preview.summary()["approval_required"]
        assert (project / "a.py").read_text(encoding="utf-8").startswith("a")
        with pytest.raises(LSPError, match="approval"):
            peer.apply_preview(preview)
        result = peer.apply_preview(preview, approved=True, expected_digest=preview.digest)
        assert result["status"] == "applied"
        assert result["publication_performed"] is False
        assert (project / "a.py").read_text(encoding="utf-8").startswith("renamed")
        assert (project / "b.py").read_text().startswith("renamed")
        assert peer.read_document("a.py")["version"] == 2
        with pytest.raises(LSPError, match="belong"):
            peer.apply_preview(preview, approved=True, expected_digest=preview.digest)


@pytest.mark.parametrize("mode", ["stale-edit", "resource", "overlap", "duplicate-target"])
def test_invalid_workspace_edits_are_never_applied(project, mode):
    with client(project, mode) as peer:
        peer.read_document("a.py")
        with pytest.raises(LSPError):
            peer.preview_rename("a.py", 0, 0, "new")
        assert (project / "a.py").read_text(encoding="utf-8").startswith("a")


def test_preview_does_not_grant_complete_read_or_allow_tampering(project):
    with client(project) as peer:
        preview = peer.preview_rename("a.py", 0, 0, "new")
        with pytest.raises(LSPError, match="complete fresh"):
            peer.apply_preview(preview, approved=True, expected_digest=preview.digest)
        peer.read_document("a.py")
        altered = replace(preview, changes=())
        with pytest.raises(LSPError, match="belong"):
            peer.apply_preview(altered, approved=True, expected_digest=preview.digest)
        (project / "a.py").write_text("foreign\n")
        with pytest.raises(SourceConflict, match="stale"):
            peer.apply_preview(preview, approved=True, expected_digest=preview.digest)
        assert (project / "a.py").read_text() == "foreign\n"


def test_context_scope_and_read_requirements_even_if_enforcement_disabled(project):
    with client(project) as peer:
        preview = peer.preview_rename("a.py", 0, 0, "new")
        ctx = SimpleNamespace(project_root=project, edits_enabled=True, mutation_scope=frozenset({"b.py"}),
                              read_coverage=lambda *_: {"read_complete": True})
        with pytest.raises(LSPError, match="scope"):
            peer.apply_preview(preview, approved=True, expected_digest=preview.digest, context=ctx)
        ctx.mutation_scope = None
        ctx.read_coverage = lambda *_: {"read_complete": False}
        with pytest.raises(LSPError, match="complete fresh"):
            peer.apply_preview(preview, approved=True, expected_digest=preview.digest, context=ctx)


def test_multi_path_failure_rolls_back_and_keeps_journal(project, monkeypatch):
    import atomic_flow.agents.file_transaction as transaction

    with client(project) as peer:
        original = {rel: (project / rel).read_bytes() for rel in ("a.py", "b.py")}
        for rel in original:
            peer.read_document(rel)
        preview = peer.preview_rename("a.py", 0, 0, "new")
        write = transaction._write
        calls = 0

        def fail_second(root, rel, data, expected):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic write failure")
            return write(root, rel, data, expected)

        monkeypatch.setattr(transaction, "_write", fail_second)
        with pytest.raises(LSPError, match="source restored"):
            peer.apply_preview(preview, approved=True, expected_digest=preview.digest)
        assert {rel: (project / rel).read_bytes() for rel in original} == original
        journals = list((project.parent / "state" / "lsp-transactions").glob("*/file-transaction.json"))
        assert len(journals) == 1
        assert json.loads(journals[0].read_text())["status"] == "undone"


def test_refresh_race_preserves_foreign_bytes_and_reports_failed_rollback(project):
    with client(project) as peer:
        peer.read_document("a.py")
        preview = peer.preview_rename("a.py", 0, 0, "new")

        def refresh():
            (project / "a.py").write_text("foreign\n")

        ctx = SimpleNamespace(project_root=project, edits_enabled=True, mutation_scope=None,
                              read_coverage=lambda *_: {"read_complete": True}, refresh=refresh, cancel_check=lambda: None)
        with pytest.raises(LSPError, match="rollback failed"):
            peer.apply_preview(preview, approved=True, expected_digest=preview.digest, context=ctx)
        assert (project / "a.py").read_text() == "foreign\n"


def test_explicit_interrupted_transaction_recovery(project):
    from atomic_flow.agents import file_transaction
    from atomic_flow.config import user_dir

    storage = user_dir() / "lsp-transactions"
    before = {"a.py": (project / "a.py").read_bytes(), "b.py": (project / "b.py").read_bytes()}
    after = {"a.py": b"new a\n", "b.py": b"new b\n"}
    identity, state = file_transaction.begin(storage, project, before, after)
    file_transaction._write(project, "a.py", after["a.py"], state["changes"]["a.py"]["before"])
    with pytest.raises(LSPError, match="approval"):
        recover_lsp_edit(project, identity)
    assert recover_lsp_edit(project, identity, approved=True)["status"] == "undone"
    assert (project / "a.py").read_bytes() == before["a.py"]
    assert (project / "b.py").read_bytes() == before["b.py"]


def test_operation_cancel_interrupts_lock_wait_and_restores_callback(project):
    peer = client(project)
    original = peer.cancel_check
    locked, release = threading.Event(), threading.Event()

    def owner():
        with peer._serial:
            locked.set()
            release.wait(3)

    worker = threading.Thread(target=owner)
    worker.start()
    assert locked.wait(2)
    calls = 0

    def cancel_wait():
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise InterruptedError("cancel queued operation")

    try:
        with pytest.raises(InterruptedError):
            with peer.operation(cancel_check=cancel_wait):
                pytest.fail("cancelled waiter acquired client")
    finally:
        release.set()
        worker.join()
    assert peer.cancel_check is original
    with peer.operation(cancel_check=lambda: None):
        assert peer.cancel_check is not original
    assert peer.cancel_check is original


def test_operation_binds_cancellation_to_active_request(project):
    with client(project, "timeout") as peer:
        original = peer.cancel_check
        cancelled = threading.Event()
        timer = threading.Timer(0.1, cancelled.set)

        def check():
            if cancelled.is_set():
                raise InterruptedError("request cancelled")

        timer.start()
        with pytest.raises(InterruptedError), peer.operation(cancel_check=check):
            peer.definition("a.py", 0, 0)
        timer.join()
        assert peer.cancel_check is original
        assert peer._process.poll() is not None
