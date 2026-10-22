"""Clarifications fail closed; result pages remain data with verified identity."""
import json
from types import SimpleNamespace

import pytest

from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.agents.result_store import LargeResultStore, ResultStoreError
from atomic_flow.agents.tools.ask_user import AskUserArgs, AskUserTool
from atomic_flow.agents.tools.read_result import ReadResultArgs, ReadResultTool


def context(**kwargs):
    return SimpleNamespace(cancel_check=lambda: None, **kwargs)


def test_question_explicit_callback_and_journal():
    events = []
    ctx = context(question_callback=lambda question: "Use SQLite", journal_event=lambda kind, payload: events.append((kind, payload)))
    result = AskUserTool().run(AskUserArgs(question="Which database?", options=["SQLite", "Postgres"]), ctx)
    assert result["answer"] == "Use SQLite"
    assert result["outcome"] == "success"
    assert [kind for kind, _ in events] == ["question", "answer"]
    assert events[0][1]["question_id"] == events[1][1]["question_id"]
    assert AskUserTool.contract.interaction and not AskUserTool.contract.idempotent


@pytest.mark.parametrize("callback", [None, lambda question: None, lambda question: ""])
def test_question_never_invents_answer(callback):
    result = AskUserTool().run(AskUserArgs(question="Choose?"), context(question_callback=callback, yes=True))
    assert result["error"]
    assert "answer" not in result
    assert result["outcome"] in {"error", "denied"}


def test_question_checks_cancellation_after_reply():
    cancelled = False

    def answer(question):
        nonlocal cancelled
        cancelled = True
        return "yes"

    def check():
        if cancelled:
            raise TaskCancelled("stopped")

    ctx = context(question_callback=answer)
    ctx.cancel_check = check
    with pytest.raises(TaskCancelled):
        AskUserTool().run(AskUserArgs(question="Continue?"), ctx)


def test_result_unicode_pages_reconstruct_redacted_original(tmp_path):
    store = LargeResultStore(tmp_path)
    preview = store.put({"stdout": "日本語🧪" * 80, "password": "do-not-store-this", "outcome": "error", "error": "failed",
                         "command": "pytest", "exit_code": 1})
    parts, offset = [], 0
    while True:
        page = store.read(preview["result_id"], offset, 17)
        parts.append(page["data"])
        assert page["result_sha256"] == preview["result_sha256"]
        assert page["original_outcome"] == "error"
        assert page["original_verification"] == {"pytest": "failed"}
        assert page["data_only"] and "command" not in page and "checks" not in page
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    original = json.loads("".join(parts))
    assert original["stdout"] == "日本語🧪" * 80
    assert original["password"] == "[REDACTED]"
    assert "do-not-store-this" not in next(store.directory.glob("*.json")).read_text(encoding="utf-8")
    assert preview["outcome"] == "error" and preview["verification"] == {"pytest": "failed"}


def test_result_id_request_boundary_and_tampering(tmp_path):
    first, second = LargeResultStore(tmp_path), LargeResultStore(tmp_path)
    preview = first.put({"text": "hello"})
    for bad in ("../../secret", "f" * 32, preview["result_id"]):
        with pytest.raises(ResultStoreError):
            second.read(bad)
    path = first.directory / f"{preview['result_id']}.json"
    original = path.read_bytes()
    path.write_bytes(original.replace(b"hello", b"jello"))
    with pytest.raises(ResultStoreError, match="checksum"):
        first.read(preview["result_id"])
    path.unlink()
    with pytest.raises(ResultStoreError, match="unavailable"):
        first.read(preview["result_id"])


def test_result_storage_bounds_do_not_evict_existing_result(tmp_path):
    store = LargeResultStore(tmp_path, max_result_bytes=256, max_total_bytes=512, max_results=1)
    preview = store.put({"text": "small"})
    with pytest.raises(ResultStoreError, match="capacity"):
        store.put({"text": "next"})
    with pytest.raises(ResultStoreError, match="byte limit"):
        store.put({"text": "🧪" * 256})
    with pytest.raises(ResultStoreError, match="outcome"):
        store.put({"outcome": ["success"]})
    assert "small" in store.read(preview["result_id"])["data"]
    for offset, limit in ((-1, 5), (10_000, 5), (0, 0), (0, 16385)):
        with pytest.raises(ResultStoreError):
            store.read(preview["result_id"], offset, limit)


def test_read_result_tool_only_reads_current_store(tmp_path):
    tool = ReadResultTool()
    args = ReadResultArgs(result_id="a" * 32)
    assert tool.run(args, context())["error"]
    store = LargeResultStore(tmp_path)
    preview = store.put({"status": "applied", "checks": ["syntax", "pytest"], "content_hash": "abc"})
    result = tool.run(ReadResultArgs(result_id=preview["result_id"]), context(result_store=store))
    assert result["original_verification"] == {"syntax": "passed", "pytest": "passed"}
    assert "status" not in result and "checks" not in result
    assert "error" in tool.run(args, context(result_store=store))
