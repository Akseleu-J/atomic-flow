"""Bounded journal reads retain strict committed/tail and prefix integrity rules."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from atomic_flow.sessions import MAX_EVENT_BYTES, SessionJournal
from atomic_flow.state import StateError


def test_read_and_recovery_stream_long_uncommitted_tail_without_whole_file_copy(tmp_path, monkeypatch):
    journal = SessionJournal.create(tmp_path)
    journal.append("tool_request", {"tool": "write"}, call_id="write.1")
    tail = b"malformed-uncommitted:" + b"x" * (2 * MAX_EVENT_BYTES + 17)
    with journal.path.open("ab") as handle:
        handle.write(tail)
    original = Path.read_bytes

    def reject_whole_journal(path):
        if path == journal.path:
            raise AssertionError("journal reads must stream")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", reject_whole_journal)
    snapshot = journal.read()
    assert snapshot.tail_bytes == len(tail) and snapshot.pending_call_ids == ["write.1"]
    with pytest.raises(StateError, match="interrupted tail|size limit"):
        journal.append("completed", {})
    backup = journal.recover_tail()
    assert backup.read_bytes() == tail
    snapshot = journal.read()
    assert not snapshot.incomplete_tail and snapshot.pending_call_ids == ["write.1"]
    assert snapshot.events[-1]["event_type"] == "tail_recovered"
    journal.append("tool_result", {}, call_id="write.1", outcome="unknown")
    assert journal.read().unresolved_call_ids == ["write.1"]


def test_oversized_committed_line_cannot_be_misclassified_as_recoverable_tail(tmp_path):
    journal = SessionJournal.create(tmp_path)
    with journal.path.open("ab") as handle:
        handle.write(b"x" * (2 * MAX_EVENT_BYTES + 17) + b"\n" + b"uncommitted")
    with pytest.raises(StateError, match="event 2 exceeds size limit"):
        journal.read()
    before = journal.path.stat().st_size
    with pytest.raises(StateError, match="event 2 exceeds size limit"):
        journal.recover_tail()
    assert journal.path.stat().st_size == before
    assert not list(journal.path.parent.glob("*.tail"))


def test_newline_boundary_oversized_commit_is_refused(tmp_path):
    journal = SessionJournal.create(tmp_path)
    with journal.path.open("ab") as handle:
        handle.write(b"x" * MAX_EVENT_BYTES + b"\n")
    with pytest.raises(StateError, match="size limit"):
        journal.read()


def test_same_size_tamper_with_restored_mtime_still_blocks_append(tmp_path):
    journal = SessionJournal.create(tmp_path)
    journal.append("question", {"text": "original"})
    known = journal.path.read_bytes()
    metadata = journal.path.stat()
    journal.path.write_bytes(known.replace(b"original", b"tampered"))
    os.utime(journal.path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    with pytest.raises(StateError, match="validated session prefix changed"):
        journal.append("completed", {})
    journal.path.write_bytes(known)
    journal.append("completed", {})


def test_separate_writer_append_is_validated_and_expected_sequence_remains_strict(tmp_path):
    journal = SessionJournal.create(tmp_path)
    journal.read()
    second = SessionJournal.open(journal.session_id)
    second.append("question", {"text": "external cooperating writer"})
    with pytest.raises(StateError, match="session changed"):
        journal.append("completed", {}, expected_sequence=1)
    journal.append("completed", {}, expected_sequence=2)
    assert [event["sequence"] for event in journal.read().events] == [1, 2, 3]


def test_growth_during_read_cannot_bypass_session_byte_admission(tmp_path, monkeypatch):
    import atomic_flow.sessions as module

    journal = SessionJournal.create(tmp_path)
    admitted_size = journal.path.stat().st_size
    with journal.path.open("ab") as handle:
        handle.write(b"x" * 100)
    real_stat = Path.stat
    monkeypatch.setattr(module, "MAX_SESSION_BYTES", admitted_size + 50)
    monkeypatch.setattr(Path, "stat", lambda path, *a, **kw:
                        SimpleNamespace(st_size=admitted_size) if path == journal.path else real_stat(path, *a, **kw))
    with pytest.raises(StateError, match="session exceeds"):
        journal.read()


def test_recovery_reads_committed_prefix_only_once(tmp_path):
    from tools.resource_scale_smoke import count_reads

    journal = SessionJournal.create(tmp_path)
    for index in range(8):
        journal.append("question", {"text": "question", "index": index})
    tail = b"uncommitted:" + b"x" * (MAX_EVENT_BYTES + 19)
    with journal.path.open("ab") as handle:
        handle.write(tail)
    before_size = journal.path.stat().st_size
    with count_reads(journal.path) as counters:
        journal.recover_tail()
    # One complete streaming validation plus one direct tail-only backup copy.
    assert counters["read_bytes"] == before_size + len(tail)
