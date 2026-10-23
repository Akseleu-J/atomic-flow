from __future__ import annotations

import json
from dataclasses import replace

import pytest

from atomic_flow.agents import file_transaction
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.source import SourceConflict, digest
from atomic_flow.agents.tools.notebook import (
    NotebookEditArgs,
    NotebookEditTool,
    NotebookReadArgs,
    NotebookReadTool,
)
from atomic_flow.agents.undo import UndoStack
from atomic_flow.notebooks import NotebookDocument
from atomic_flow.tree.defaults import MAX_FILE_BYTES


def cell(cell_id="first", source="print(1)", *, kind="code"):
    value = {"id": cell_id, "cell_type": kind, "source": source, "metadata": {"trusted": True}}
    if kind == "code":
        value.update(outputs=[{"output_type": "stream", "name": "stdout", "text": "SAVED_OUTPUT_SECRET"}],
                     execution_count=9)
    return value


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setenv("ATOMIC_FLOW_HOME", str(tmp_path / "state"))
    return root


def write(project, cells=None, *, path="book.ipynb"):
    value = {"nbformat": 4, "nbformat_minor": 5, "metadata": {"signature": "PRIVATE_METADATA"},
             "cells": cells if cells is not None else [cell(), cell("second", "value = 2")]}
    target = project / path
    target.write_text(json.dumps(value), encoding="utf-8")
    return target


def apply(document, preview, tmp_path, **kwargs):
    return document.apply(preview, storage=tmp_path / "undo", approved=True,
                          preview_token=preview.preview_token, **kwargs)


def test_read_is_paginated_redacted_source_only(project):
    token = "ghp_" + "A" * 36
    target = write(project, [cell(source=f"token = '{token}'\n" + "a" * 1500)])
    original = target.read_bytes()
    document = NotebookDocument.load(project, "book.ipynb")
    pages, offset = [], 0
    while True:
        page = document.read(offset=offset, limit=256)
        pages.append(page["source"])
        assert page["outputs"] == "omitted" and page["metadata"] == "omitted"
        assert page["provenance"]["content_hash"] == digest(original)
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    text = "".join(pages)
    assert "[REDACTED]" in text and token not in text
    assert "SAVED_OUTPUT_SECRET" not in text and "PRIVATE_METADATA" not in text
    assert target.read_bytes() == original


def test_reorder_preserves_ids_clears_all_saved_output_and_trust(project, tmp_path):
    target = write(project)
    document = NotebookDocument.load(project, "book.ipynb")
    preview = document.preview({"action": "move", "cell_id": "second", "before_id": "first"})
    assert target.read_bytes() == document._data
    result = apply(document, preview, tmp_path)
    assert result["status"] == "applied" and result["verification"]["notebook_execution"] == "not_run"
    value = json.loads(target.read_text())
    assert [c["id"] for c in value["cells"]] == ["second", "first"]
    assert all(c["outputs"] == [] and c["execution_count"] is None and "trusted" not in c["metadata"]
               for c in value["cells"])
    assert "signature" not in value["metadata"]
    assert file_transaction.undo(tmp_path / "undo", project, result["edit_id"])
    assert target.read_bytes() == document._data


def test_host_normalizes_legacy_ids_only_with_explicit_approval(project, tmp_path):
    legacy = cell()
    del legacy["id"]
    target = write(project, [legacy, cell("already-present")])
    original = target.read_bytes()
    document = NotebookDocument.load(project, "book.ipynb")
    assert document.read()["missing_ids"] == 1 and target.read_bytes() == original
    with pytest.raises(SourceConflict, match="normalize"):
        document.preview({"action": "set_source", "cell_id": "already-present", "source": "x"})
    with pytest.raises(SourceConflict, match="host approval"):
        document.preview({"action": "normalize_ids"})
    preview = document.preview_normalize_ids()
    with pytest.raises(SourceConflict, match="host approval"):
        apply(document, preview, tmp_path)
    assert apply(document, preview, tmp_path, allow_normalization=True)["status"] == "applied"
    cells = json.loads(target.read_text())["cells"]
    assert cells[0]["id"].startswith("af_") and cells[1]["id"] == "already-present"


@pytest.mark.parametrize("operation", [
    {"action": "set_source", "cell_id": "first", "source": "print(42)"},
    {"action": "delete", "cell_id": "first"},
    {"action": "insert", "cell_id": "new", "cell_type": "markdown", "source": "# Title", "before_id": "second"},
])
def test_cell_mutations_bind_whole_file_hash_and_operation(project, tmp_path, operation):
    target = write(project)
    document = NotebookDocument.load(project, "book.ipynb")
    preview = document.preview(operation)
    with pytest.raises(SourceConflict, match="approval"):
        document.apply(preview, storage=tmp_path / "undo", preview_token=preview.preview_token)
    with pytest.raises(SourceConflict, match="token"):
        document.apply(preview, storage=tmp_path / "undo", approved=True, preview_token="0" * 64)
    with pytest.raises(SourceConflict, match="token"):
        apply(document, replace(preview, _after=b"tamper"), tmp_path)
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(SourceConflict, match="changed"):
        apply(document, preview, tmp_path)


@pytest.mark.parametrize("cells", [[cell(), cell()], [cell("bad.id")], [dict(cell(), id=None)],
                                  [dict(cell(), source=123)], [cell(source="x\x00y")],
                                  [dict(cell(), outputs=[{"output_type": "javascript"}])]])
def test_invalid_notebooks_fail_closed(project, cells):
    write(project, cells)
    with pytest.raises(SourceConflict):
        NotebookDocument.load(project, "book.ipynb")


@pytest.mark.parametrize("path", [".private.ipynb", "secrets.ipynb", "NUL.ipynb", "../outside.ipynb",
                                  "book.ipynb:stream", "book.py", "a\\book.ipynb"])
def test_private_and_ambiguous_paths_are_not_notebook_source(project, path):
    write(project)
    with pytest.raises(SourceConflict):
        NotebookDocument.load(project, path)


def test_ignored_oversized_duplicate_json_and_wrong_version(project):
    target = write(project)
    (project / ".gitignore").write_text("book.ipynb\n")
    with pytest.raises(SourceConflict, match="excluded"):
        NotebookDocument.load(project, "book.ipynb")
    (project / ".gitignore").unlink()
    for data in [b" " * (MAX_FILE_BYTES + 1), b'{"nbformat":4,"nbformat":4}',
                 b'{"nbformat":5}', b'{"x":NaN}']:
        target.write_bytes(data)
        with pytest.raises(SourceConflict):
            NotebookDocument.load(project, "book.ipynb")


def test_refresh_failure_rolls_back_without_executing(project, tmp_path):
    target = write(project)
    before = target.read_bytes()
    document = NotebookDocument.load(project, "book.ipynb")
    preview = document.preview({"action": "set_source", "cell_id": "first", "source": "raise Exception('never execute')"})

    def broken_refresh():
        raise RuntimeError("refresh failed")

    result = apply(document, preview, tmp_path, after_write=broken_refresh)
    assert result["status"] == "rolled_back" and target.read_bytes() == before
    assert result["verification"]["notebook_execution"] == "not_run"


def test_foreign_write_during_refresh_is_not_overwritten(project, tmp_path):
    target = write(project)
    document = NotebookDocument.load(project, "book.ipynb")
    preview = document.preview({"action": "delete", "cell_id": "first"})

    def concurrent_write():
        target.write_bytes(b"foreign writer")

    result = apply(document, preview, tmp_path, after_write=concurrent_write)
    assert result["status"] == "rollback_failed" and result["side_effects_may_have_occurred"]
    assert target.read_bytes() == b"foreign writer"


def test_interrupted_transaction_recovers_original(project, tmp_path, monkeypatch):
    target = write(project)
    document = NotebookDocument.load(project, "book.ipynb")
    preview = document.preview({"action": "delete", "cell_id": "first"})
    real_apply = file_transaction.apply

    def interrupted(*args, **kwargs):
        real_apply(*args, **kwargs)
        raise RuntimeError("interrupted")

    monkeypatch.setattr(file_transaction, "apply", interrupted)
    assert apply(document, preview, tmp_path)["status"] == "rolled_back"
    assert target.read_bytes() == document._data


def test_tools_require_all_source_pages_and_bind_preview(project, tmp_path):
    write(project, [cell(source="long source" * 100)])
    ctx = ToolContext(project, {}, edits_enabled=True, enforce_read_before_edit=True,
                      undo_stack=UndoStack(tmp_path / "undo"))
    ctx.refresh = lambda: None
    read_tool, edit_tool = NotebookReadTool(), NotebookEditTool()
    page = read_tool.run(NotebookReadArgs(path="book.ipynb", limit=256), ctx)
    params = {"path": "book.ipynb", "expected_hash": page["content_hash"],
              "operation": {"action": "set_source", "cell_id": "first", "source": "new source"}}
    preview = edit_tool.run(NotebookEditArgs(**params), ctx)
    args = NotebookEditArgs(**params, preview_token=preview["preview_token"])
    with pytest.raises(OSError, match="complete current source"):
        edit_tool.run(args, ctx)
    while page["next_offset"] is not None:
        page = read_tool.run(NotebookReadArgs(path="book.ipynb", offset=page["next_offset"], limit=256), ctx)
    assert page["read_complete"]
    assert edit_tool.run(args, ctx)["status"] == "applied"
    assert not ctx.read_coverage("book.ipynb", ctx.file_hashes["book.ipynb"])["read_complete"]


def test_source_tamper_invalidates_read_proof(project):
    target = write(project)
    ctx = ToolContext(project, {})
    NotebookReadTool().run(NotebookReadArgs(path="book.ipynb"), ctx)
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(SourceConflict, match="changed"):
        NotebookReadTool().run(NotebookReadArgs(path="book.ipynb"), ctx)


def test_insert_and_delete_are_structured_and_never_execute_code(project, tmp_path):
    target = write(project)
    source = "from pathlib import Path; Path('SHOULD_NOT_EXIST').write_text('executed')"
    document = NotebookDocument.load(project, "book.ipynb")
    preview = document.preview({"action": "insert", "cell_id": "new-code", "before_id": "first",
                                "cell_type": "code", "source": source})
    assert apply(document, preview, tmp_path)["status"] == "applied"
    value = json.loads(target.read_text())
    assert value["cells"][0]["id"] == "new-code" and value["cells"][0]["source"] == source
    assert not (project / "SHOULD_NOT_EXIST").exists()
    document = NotebookDocument.load(project, "book.ipynb")
    assert apply(document, document.preview({"action": "delete", "cell_id": "new-code"}), tmp_path)["status"] == "applied"
    assert [c["id"] for c in json.loads(target.read_text())["cells"]] == ["first", "second"]


def test_preview_disallows_metadata_outputs_and_arbitrary_json_patch(project):
    write(project)
    document = NotebookDocument.load(project, "book.ipynb")
    for operation in [{"action": "set_metadata", "cell_id": "first", "metadata": {"trusted": True}},
                      {"action": "set_source", "cell_id": "first", "source": "x", "outputs": []},
                      {"action": "patch", "path": "/cells/0/outputs", "value": []}]:
        with pytest.raises(SourceConflict, match="unsupported"):
            document.preview(operation)


def test_loaded_notebook_does_not_claim_fresh_read_or_preview_after_external_edit(project):
    target = write(project)
    document = NotebookDocument.load(project, "book.ipynb")
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(SourceConflict, match="changed"):
        document.read()
    with pytest.raises(SourceConflict, match="changed"):
        document.preview({"action": "delete", "cell_id": "first"})


def test_render_race_does_not_issue_notebook_read_proof(project, monkeypatch):
    import atomic_flow.notebooks as notebooks

    target = write(project)
    ctx = ToolContext(project, {})
    original_redact = notebooks.redact

    def racing_redact(text):
        target.write_bytes(target.read_bytes() + b"\n")
        return original_redact(text)

    monkeypatch.setattr(notebooks, "redact", racing_redact)
    with pytest.raises(SourceConflict, match="changed"):
        NotebookReadTool().run(NotebookReadArgs(path="book.ipynb"), ctx)
    assert ctx.reads == []


def test_preview_race_is_rejected_after_render(project, monkeypatch):
    import atomic_flow.notebooks as notebooks

    target = write(project)
    document = NotebookDocument.load(project, "book.ipynb")
    original_encode = notebooks._encode

    def racing_encode(value):
        result = original_encode(value)
        target.write_bytes(target.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(notebooks, "_encode", racing_encode)
    with pytest.raises(SourceConflict, match="changed"):
        document.preview({"action": "delete", "cell_id": "first"})


def test_canceled_notebook_read_does_not_issue_read_proof(project):
    write(project)
    ctx = ToolContext(project, {})
    checks = 0

    def cancel_after_render():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("cancelled")

    ctx.cancel_check = cancel_after_render
    with pytest.raises(RuntimeError, match="cancelled"):
        NotebookReadTool().run(NotebookReadArgs(path="book.ipynb"), ctx)
    assert ctx.reads == []


def test_symlink_notebook_refused(project, tmp_path):
    target = write(project)
    link = project / "linked.ipynb"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable on this Windows host")
    with pytest.raises(SourceConflict, match="symlink"):
        NotebookDocument.load(project, "linked.ipynb")
