from pathlib import Path

import pytest

from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.source import SourceConflict
from atomic_flow.agents.tools.workspace import WorkspaceReadArgs, WorkspaceReadTool
from atomic_flow.workspace import WorkspaceError, WorkspaceRegistry


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("ATOMIC_FLOW_HOME", str(tmp_path / "home"))
    a, b = tmp_path / "alpha", tmp_path / "beta"
    a.mkdir()
    b.mkdir()
    for root in (a, b):
        (root / "main.py").write_text("value = 1\n", encoding="utf-8")
    registry = WorkspaceRegistry("test")
    registry.grant("alpha", a, write_paths=["main.py", "new.py"], approved=True)
    registry.grant("beta", b, write_paths=["main.py"], approved=True)
    return registry, a, b


def test_explicit_approval_and_no_implicit_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("ATOMIC_FLOW_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    root.mkdir()
    registry = WorkspaceRegistry()
    assert registry.list_roots() == []
    with pytest.raises(WorkspaceError, match="approval"):
        registry.grant("source", root)
    with pytest.raises(WorkspaceError, match="not granted"):
        registry.session().read("source", "file.py")
    with pytest.raises(WorkspaceError):
        registry.grant("C:", root, approved=True)
    with pytest.raises(WorkspaceError):
        registry.grant("source", Path("relative"), approved=True)


def test_persistent_grants_not_persistent_proofs(workspace):
    registry, a, _ = workspace
    session = registry.session()
    result = session.read("alpha", "main.py", limit=5)
    assert not result["read_complete"]
    with pytest.raises(WorkspaceError, match="complete"):
        session.preview("alpha", {"main.py": "value = 2\n"})
    result = session.read("alpha", "main.py", offset=5)
    assert result["read_complete"] and result["hash_algorithm"] == "sha256"
    assert len(result["content_hash"]) == 64
    reopened = WorkspaceRegistry("test")
    assert reopened.list_roots()[0]["path"] in {str(a), str(workspace[2])}
    with pytest.raises(WorkspaceError, match="complete"):
        reopened.session().preview("alpha", {"main.py": "value = 2\n"})
    with pytest.raises(WorkspaceError, match="complete"):
        session.preview("beta", {"main.py": "value = 2\n"})


def test_source_paths_private_traversal_and_state(workspace):
    registry, a, _ = workspace
    (a / ".env").write_text("SECRET=x")
    (a / "secrets.py").write_text("SECRET=x")
    (a / ".git").mkdir()
    (a / ".git" / "config.py").write_text("x=1")
    session = registry.session()
    for rel in ("../main.py", "main.py:stream", "dir\\main.py", ".env", "secrets.py", ".git/config.py",
                "file.txt", "./main.py", "/main.py", "CON.py"):
        with pytest.raises(SourceConflict):
            session.read("alpha", rel)
    home = registry.directory.parents[1]
    with pytest.raises(WorkspaceError, match="disjoint"):
        registry.grant("state", home, approved=True)
    with pytest.raises(WorkspaceError, match="disjoint"):
        registry.grant("parent", a.parent, approved=True)


def test_reject_overlapping_alias_and_max_roots(workspace):
    registry, a, _ = workspace
    (a / "child").mkdir()
    for path in (a, a / "child"):
        with pytest.raises(WorkspaceError, match="overlapping"):
            registry.grant("alias", path, approved=True)
    for index in range(6):
        root = a.parent / f"extra{index}"
        root.mkdir()
        registry.grant(f"extra{index}", root, approved=True)
    root = a.parent / "extra6"
    root.mkdir()
    with pytest.raises(WorkspaceError, match="root limit"):
        registry.grant("extra6", root, approved=True)


def test_scoped_preview_and_one_root_recoverable_publish(workspace):
    registry, a, b = workspace
    session = registry.session()
    session.read("alpha", "main.py")
    session.read("alpha", "new.py")
    with pytest.raises(WorkspaceError, match="write scope"):
        session.preview("alpha", {"other.py": "value = 2"})
    preview = session.preview("alpha", {"main.py": "value = 2\n", "new.py": "new = True\n"})
    with pytest.raises(WorkspaceError, match="approval"):
        session.apply(preview["preview_token"])
    result = session.apply(preview["preview_token"], approved=True)
    assert result["status"] == "published" and result["cross_root_atomic"] is False
    assert (a / "main.py").read_text() == "value = 2\n"
    assert (a / "new.py").read_text() == "new = True\n"
    assert (b / "main.py").read_text() == "value = 1\n"
    with pytest.raises(WorkspaceError, match="consumed"):
        session.apply(preview["preview_token"], approved=True)


def test_stale_read_preview_manifest_and_replacement(workspace):
    registry, a, _ = workspace
    session = registry.session()
    session.read("alpha", "main.py")
    (a / "main.py").write_text("value = 9\n")
    with pytest.raises(WorkspaceError, match="complete"):
        session.preview("alpha", {"main.py": "value = 2\n"})
    session.read("alpha", "main.py")
    with pytest.raises(WorkspaceError, match="compile"):
        session.preview("alpha", {"main.py": "def broken("})
    preview = session.preview("alpha", {"main.py": "value = 2\n"})
    (a / "unrelated.py").write_text("foreign = True\n")
    with pytest.raises(WorkspaceError, match="manifest"):
        session.apply(preview["preview_token"], approved=True)
    assert (a / "main.py").read_text() == "value = 9\n"
    preview = session.preview("alpha", {"main.py": "value = 2\n"})
    (a / "main.py").write_text("value = 99\n")
    with pytest.raises(WorkspaceError, match="after preview"):
        session.apply(preview["preview_token"], approved=True)


def test_revoke_regrant_invalidates_read_and_preview(workspace):
    registry, a, _ = workspace
    session = registry.session()
    session.read("alpha", "main.py")
    preview = session.preview("alpha", {"main.py": "value = 2\n"})
    with pytest.raises(WorkspaceError, match="approval"):
        registry.revoke("alpha")
    registry.revoke("alpha", approved=True)
    with pytest.raises(WorkspaceError):
        session.apply(preview["preview_token"], approved=True)
    registry.grant("alpha", a, write_paths=["main.py"], approved=True)
    with pytest.raises(WorkspaceError, match="complete"):
        session.preview("alpha", {"main.py": "value = 2\n"})


def test_revocation_during_publish_rolls_back(workspace, monkeypatch):
    from atomic_flow.agents.changeset import ChangeSet

    registry, a, _ = workspace
    session = registry.session()
    session.read("alpha", "main.py")
    preview = session.preview("alpha", {"main.py": "value = 2\n"})
    original = ChangeSet._write_target

    def revoke_after_write(change, rel, data):
        original(change, rel, data)
        registry.revoke("alpha", approved=True)

    monkeypatch.setattr(ChangeSet, "_write_target", revoke_after_write)
    with pytest.raises(WorkspaceError, match="not granted"):
        session.apply(preview["preview_token"], approved=True)
    assert (a / "main.py").read_text() == "value = 1\n"


def test_host_final_check_failure_and_workspace_tamper(workspace):
    registry, a, _ = workspace
    session = registry.session()
    session.read("alpha", "main.py")
    preview = session.preview("alpha", {"main.py": "value = 2\n"})
    with pytest.raises(WorkspaceError, match="final checks"):
        session.apply(preview["preview_token"], approved=True, final_check=lambda _: False)
    assert (a / "main.py").read_text() == "value = 1\n"
    preview = session.preview("alpha", {"main.py": "value = 2\n"})

    def tamper(root):
        (root / "main.py").write_text("value = 99\n")
        return True

    with pytest.raises(SourceConflict):
        session.apply(preview["preview_token"], approved=True, final_check=tamper)
    assert (a / "main.py").read_text() == "value = 1\n"


def test_delete_absence_proof_listing_and_tool(workspace):
    registry, a, _ = workspace
    session = registry.session()
    assert session.list_files("alpha")["files"] == ["main.py"]
    (a / "second.py").write_text("x=1")
    assert session.list_files("alpha", limit=1)["truncated"]
    ctx = ToolContext(a, {})
    tool = WorkspaceReadTool()
    args = WorkspaceReadArgs(root_id="alpha", path="main.py")
    assert "error" in tool.run(args, ctx)
    ctx.workspace_session = session
    assert tool.run(args, ctx)["read_complete"]
    # Model-facing results do not mint the host session's mutation proof.
    with pytest.raises(WorkspaceError, match="read the complete"):
        session.preview("alpha", {"main.py": None})
    session.read("alpha", "main.py")
    preview = session.preview("alpha", {"main.py": None})
    assert session.apply(preview["preview_token"], approved=True)["status"] == "published"
    assert not (a / "main.py").exists()


def test_links_and_storage_alias_refused(workspace, tmp_path, monkeypatch):
    registry, a, b = workspace
    try:
        (a / "alias.py").symlink_to(b / "main.py")
        (a.parent / "aliasroot").symlink_to(a, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(SourceConflict):
        registry.session().read("alpha", "alias.py")
    with pytest.raises(WorkspaceError):
        registry.grant("alias", a.parent / "aliasroot", approved=True)
    monkeypatch.setenv("ATOMIC_FLOW_HOME", str(a.parent / "aliasroot" / "state"))
    with pytest.raises(WorkspaceError):
        WorkspaceRegistry()


def test_hardlink_source_alias_refused(workspace):
    registry, a, b = workspace
    try:
        (a / "alias.py").hardlink_to(b / "main.py")
    except OSError:
        pytest.skip("hard links unavailable")
    with pytest.raises(WorkspaceError, match="hard-linked"):
        registry.session().read("alpha", "alias.py")


def test_replaced_root_fails_closed_but_can_be_revoked(workspace):
    registry, a, _ = workspace
    original = a.parent / "oldalpha"
    a.rename(original)
    a.mkdir()
    (a / "main.py").write_text("foreign = True\n")
    with pytest.raises(WorkspaceError, match="invalid or unavailable"):
        registry.session().read("alpha", "main.py")
    assert registry.revoke("alpha", approved=True)["revoked"]


def test_unavailable_root_can_be_revoked(workspace):
    registry, a, _ = workspace
    a.rename(a.parent / "oldalpha")
    assert registry.revoke("alpha", approved=True)["revoked"]


def test_listing_and_preview_bound_empty_directory_traversal(workspace, monkeypatch):
    import atomic_flow.workspace as module

    registry, a, _ = workspace
    for index in range(8):
        (a / f"empty{index}").mkdir()
    monkeypatch.setattr(module, "_SCAN_ENTRIES", 3)
    session = registry.session()
    result = session.list_files("alpha")
    assert result["truncated"] and "entry limit" in result["limit_reason"]
    session.read("alpha", "main.py")
    with pytest.raises(module.WorkspaceScanLimit, match="entry limit"):
        session.preview("alpha", {"main.py": "value=2"})


def test_listing_and_preview_cooperative_cancellation(workspace):
    registry, _, _ = workspace
    session = registry.session()
    session.read("alpha", "main.py")

    def cancelled():
        raise RuntimeError("host cancellation")

    with pytest.raises(RuntimeError, match="host cancellation"):
        session.list_files("alpha", cancel_check=cancelled)
    with pytest.raises(RuntimeError, match="host cancellation"):
        session.preview("alpha", {"main.py": "value=2"}, cancel_check=cancelled)


def test_scan_deadline_and_manifest_bytes_are_explicit(workspace, monkeypatch):
    import atomic_flow.workspace as module

    registry, _, _ = workspace
    session = registry.session()
    session.read("alpha", "main.py")
    monkeypatch.setattr(module, "_SCAN_SECONDS", 0)
    assert "deadline" in session.list_files("alpha")["limit_reason"]
    with pytest.raises(module.WorkspaceScanLimit, match="deadline"):
        session.preview("alpha", {"main.py": "value=2"})
    monkeypatch.setattr(module, "_SCAN_SECONDS", 5)
    monkeypatch.setattr(module, "_MANIFEST_TOTAL_BYTES", 2)
    with pytest.raises(module.WorkspaceScanLimit, match="byte cap"):
        session.preview("alpha", {"main.py": "value=2"})
