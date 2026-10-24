"""Host-only staged binding never grants ordinary clients private-state access."""
import os
import sys

import pytest

from atomic_flow.lsp import LSPClient, LSPError


def test_staged_admission_requires_explicit_host_authority(tmp_path, monkeypatch):
    state = tmp_path / "state"
    workspace = state / "changesets" / "staged" / "workspace"
    workspace.mkdir(parents=True)
    source = workspace / "sample.py"
    source.write_text("x = 1\n")
    monkeypatch.setenv("ATOMIC_FLOW_HOME", str(state))
    ordinary = LSPClient(workspace, [sys.executable], approved=True)
    with pytest.raises(LSPError, match="private"):
        ordinary._path("sample.py")
    staged = LSPClient(workspace, [sys.executable], approved=True, private_workspace=True)
    assert staged._path("sample.py") == source
    for denied in ("../outside.py", ".env", "credentials.py"):
        with pytest.raises(LSPError):
            staged._path(denied)


@pytest.mark.skipif(os.name != "nt", reason="Windows drive URI spelling")
def test_windows_analyzer_lowercase_drive_is_canonical(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n")
    peer = LSPClient(tmp_path, [sys.executable], approved=True)
    uri = source.as_uri()
    lower_drive = uri[:8] + uri[8].lower() + uri[9:]
    assert peer._rel(lower_drive) == "sample.py"
