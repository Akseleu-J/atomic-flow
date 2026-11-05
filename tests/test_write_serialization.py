"""W1 write-correctness: one root-wide mutation lease serializes every source writer.

Cross-process and cross-thread writers of one project root are mutually excluded; the
same thread may nest guarded calls (publish -> replace_checked) without self-deadlock.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from atomic_flow.agents import file_transaction
from atomic_flow.agents.changeset import ChangeSet, ChangeSetConflict
from atomic_flow.agents.source import SourceConflict, confined_path, digest, replace_checked
from atomic_flow.agents.undo import UndoStack
from atomic_flow.durability import atomic_bytes, write_bytes
from atomic_flow.rewind import apply_rewind, create_snapshot, preview_rewind
from atomic_flow.source_lock import source_mutation_lease

BUSY = "mutated by another writer"
BASELINE = b"value = 'baseline'\n"


def baseline(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "sample.py").write_bytes(BASELINE)
    return root


class HeldLease:
    """Acquires source_mutation_lease(root) on a worker thread until released."""

    def __init__(self, root: Path):
        self.acquired = threading.Event()
        self._release = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, args=(root,), daemon=True)

    def _run(self, root: Path):
        try:
            with source_mutation_lease(root):
                self.acquired.set()
                self._release.wait(timeout=30)
        except BaseException as exc:  # surfaced to the main thread on exit
            self._error = exc
            self.acquired.set()

    def __enter__(self) -> HeldLease:
        self._thread.start()
        assert self.acquired.wait(timeout=10), "lease holder thread never started"
        return self

    def __exit__(self, *exc_info):
        self._release.set()
        self._thread.join(timeout=10)
        assert not self._thread.is_alive(), "lease holder thread did not exit"
        if self._error is not None:
            raise self._error


def test_concurrent_publications_are_serialized_both_versions_preserved(tmp_path, monkeypatch):
    root = baseline(tmp_path)
    a, b = ChangeSet.create(root), ChangeSet.create(root)
    for change, value in ((a, b"value = 'A'\n"), (b, b"value = 'B'\n")):
        (change.workspace / "sample.py").write_bytes(value)
        change.track("sample.py")
    target = (root / "sample.py").resolve()
    entered, release = threading.Event(), threading.Event()
    hit = False
    original_replace = Path.replace

    def pause_after_final_check(source, destination):
        nonlocal hit
        if not hit and Path(destination).resolve() == target:
            hit = True  # thread 1 holds the lease in A's post-check pre-replace window
            entered.set()
            assert release.wait(timeout=10), "publication window was never released"
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", pause_after_final_check)
    publisher = threading.Thread(target=a.publish, daemon=True)
    publisher.start()
    assert entered.wait(timeout=10), "publication never reached its final replace"
    # Thread 2 must be refused while A holds the root lease; none of B's writes may run.
    with pytest.raises(SourceConflict, match=BUSY):
        b.publish()
    assert b.state["status"] == "active"
    assert target.read_bytes() == BASELINE
    assert (b.workspace / "sample.py").read_bytes() == b"value = 'B'\n"  # B's version stays recoverable
    release.set()
    publisher.join(timeout=10)
    assert not publisher.is_alive()
    assert a.state["status"] == "published"
    assert target.read_bytes() == b"value = 'A'\n"  # exactly one publication won the window
    # Retrying B against the moved baseline must refuse a blind overwrite...
    with pytest.raises(ChangeSetConflict, match="original project changed"):
        b.publish()
    # ...and B's exact bytes still publish through a fresh post-A changeset.
    c = ChangeSet.create(root)
    (c.workspace / "sample.py").write_bytes(b"value = 'B'\n")
    c.track("sample.py")
    c.publish()
    assert target.read_bytes() == b"value = 'B'\n"


def test_cross_process_publication_conflict(tmp_path):
    root = baseline(tmp_path)
    change = ChangeSet.create(root)
    (change.workspace / "sample.py").write_bytes(b"value = 'edited'\n")
    change.track("sample.py")
    # The venv may default to another checkout; force this repository's source tree.
    src = str(Path(__file__).resolve().parents[1] / "src")
    script = tmp_path / "holder.py"
    script.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {src!r})\n"
        "from pathlib import Path\n"
        "from atomic_flow.source_lock import source_mutation_lease\n"
        "with source_mutation_lease(Path(sys.argv[1])):\n"
        "    Path(sys.argv[2]).write_text('READY')\n"
        "    for _ in range(600):\n"
        "        if Path(sys.argv[3]).exists():\n"
        "            break\n"
        "        time.sleep(0.05)\n",
        encoding="utf-8",
    )
    ready, go = tmp_path / "ready.txt", tmp_path / "go.txt"
    env = {**os.environ, "ATOMIC_FLOW_HOME": str(tmp_path / "home")}
    process = subprocess.Popen([sys.executable, str(script), str(root), str(ready), str(go)],
                               cwd=str(tmp_path), env=env)
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), f"subprocess never acquired the lease (rc={process.poll()})"
        with pytest.raises(SourceConflict, match=BUSY):
            change.publish()
        assert change.state["status"] == "active"
        assert (root / "sample.py").read_bytes() == BASELINE
    finally:
        go.write_text("go", encoding="utf-8")
        process.wait(timeout=30)
    change.publish()  # the OS lock died with the holder process; publication now proceeds
    assert change.state["status"] == "published"
    assert (root / "sample.py").read_bytes() == b"value = 'edited'\n"


def test_same_thread_nested_mutation_is_reentrant(tmp_path):
    root = baseline(tmp_path)
    with source_mutation_lease(root):
        with source_mutation_lease(root):  # documented contract: nested guards are reentrant
            replace_checked(root, "sample.py", b"value = 'nested'\n", digest(BASELINE))
    assert (root / "sample.py").read_bytes() == b"value = 'nested'\n"
    # A whole-source publication is the one exception: it never nests in this thread,
    # so the audit's same-thread interleaved double publication is refused, not run.
    with source_mutation_lease(root):
        with pytest.raises(SourceConflict, match="cannot nest"):
            with source_mutation_lease(root, publication=True):
                pass


def test_audit_rt01_same_thread_interleaved_publication_refused(tmp_path, monkeypatch):
    """Audit RT-01 replay: a second publication of the same project, run from inside
    the first publication's own thread at the real Path.replace boundary, must be
    refused instead of both succeeding with one edit silently lost.

    An OS-level lease alone cannot distinguish this from legitimate same-thread
    nesting (publish -> replace_checked), so ChangeSet.publish/recover take the lease
    with publication=True: entering a publication while this thread already holds the
    root is a SourceConflict. Refused means nothing was mutated: the interrupted
    changeset rolls back from its backups, the nested one stays active and publishes
    sequentially afterwards. Residual boundary, documented honestly: same-thread
    nesting of ordinary helpers (a raw replace_checked/file_transaction) inside a
    publication remains reentrant by design — the lease cannot tell it apart from
    legitimate nesting like edit_node -> undo. The exclusion contract covers
    cross-thread, cross-process and nested publications of one project root.
    """
    root = baseline(tmp_path)
    a, b = ChangeSet.create(root), ChangeSet.create(root)
    for change, value in ((a, b"value = 'A'\n"), (b, b"value = 'B'\n")):
        (change.workspace / "sample.py").write_bytes(value)
        change.track("sample.py")
    target = (root / "sample.py").resolve()
    injected = False
    original_replace = Path.replace

    def interleave(source, destination):
        nonlocal injected
        if not injected and Path(destination).resolve() == target:
            injected = True  # B tries to publish fully between A's final check and A's replace
            b.publish()
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", interleave)
    with pytest.raises(SourceConflict, match="cannot nest"):
        a.publish()
    assert injected  # the interleaving really fired at the replace boundary
    # Neither version was silently lost: A recovered to baseline, B never mutated.
    assert a.state["status"] == "rolled_back"
    assert b.state["status"] == "active"
    assert target.read_bytes() == BASELINE
    assert not list(root.glob("*.atomic-tmp"))  # A's aborted replace left no debris
    b.publish()  # the refused writer publishes sequentially once the window is gone
    assert b.state["status"] == "published"
    assert target.read_bytes() == b"value = 'B'\n"


def test_serialization_covers_undo_rewind_and_transactions(tmp_path):
    # UndoStack.undo, file_transaction.apply/undo and rewind.apply_rewind/recover_rewind carry
    # the same root guard; LSP/notebook edits funnel through file_transaction under it too.
    root = baseline(tmp_path)
    before_undo, after_undo, txn_bytes, rewound = BASELINE, b"value = 'undo-me'\n", b"value = 'txn'\n", b"value = 'rewound'\n"
    stack = UndoStack(tmp_path / "undo")
    record = stack.push("sample.py", "n1", "old", "new", project_root=root, before=before_undo, after=after_undo)
    storage = tmp_path / "transactions"
    edit_id, state = file_transaction.begin(storage, root, {"sample.py": before_undo}, {"sample.py": txn_bytes})
    (root / "sample.py").write_bytes(after_undo)  # the edit the undo record describes
    snap = create_snapshot(root, "before_edits", ["sample.py"])
    (root / "sample.py").write_bytes(rewound)
    token = preview_rewind(root, snap["snapshot_id"])["preview_token"]
    with HeldLease(root):
        for operation in (
            lambda: stack.undo(root, record.edit_id),
            lambda: file_transaction.apply(storage, root, edit_id, state, {"sample.py": txn_bytes}, lambda: None),
            lambda: apply_rewind(root, snap["snapshot_id"], token),
        ):
            with pytest.raises(SourceConflict, match=BUSY):
                operation()
        assert (root / "sample.py").read_bytes() == rewound  # refusals mutated nothing
    # With the lease free the same three operations all succeed in a valid order.
    assert apply_rewind(root, snap["snapshot_id"], token)["status"] == "applied"
    assert (root / "sample.py").read_bytes() == after_undo
    assert stack.undo(root, record.edit_id) is not None
    assert (root / "sample.py").read_bytes() == before_undo
    file_transaction.apply(storage, root, edit_id, state, {"sample.py": txn_bytes}, lambda: None)
    assert (root / "sample.py").read_bytes() == txn_bytes


def test_lock_file_is_reserved_host_state(tmp_path):
    root = baseline(tmp_path)
    for rel in (".atomic-flow-lock", ".ATOMIC-FLOW-LOCK"):  # the reservation is case-insensitive
        with pytest.raises(SourceConflict, match="reserved host state"):
            confined_path(root, rel)
    with pytest.raises(SourceConflict, match="reserved host state"):
        replace_checked(root, ".atomic-flow-lock", b"host\n", None)
    lock = root / ".atomic-flow-lock"
    assert not lock.exists() or lock.read_bytes() != b"host\n"  # only the lease may own it


def test_durability_helpers(tmp_path, monkeypatch):
    plain = tmp_path / "plain.bin"
    write_bytes(plain, b"exact-bytes")
    assert plain.read_bytes() == b"exact-bytes"
    target = tmp_path / "target.bin"
    target.write_bytes(b"old")
    atomic_bytes(target, b"new")
    assert target.read_bytes() == b"new"
    assert not list(tmp_path.glob("*.tmp"))  # success leaves no temporary file

    def failing_replace(source, destination):
        raise OSError("replace refused")

    monkeypatch.setattr(Path, "replace", failing_replace)
    with pytest.raises(OSError, match="replace refused"):
        atomic_bytes(target, b"rejected")
    assert target.read_bytes() == b"new"  # failed replacement keeps old content
    assert not list(tmp_path.glob("*.tmp"))  # and cleans its temporary file
