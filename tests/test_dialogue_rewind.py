"""Continuation is advisory; rewind is explicit and never overwrites foreign bytes."""
import json

import pytest

from atomic_flow.agents.source import SourceConflict
from atomic_flow.config import user_dir
from atomic_flow.dialogue import fork_session, merge_continuation_evidence, prepare_continuation, record_dialogue_state
from atomic_flow.rewind import apply_rewind, create_snapshot, preview_rewind, recover_rewind
from atomic_flow.sessions import SessionJournal
from atomic_flow.state import StateError


def completed(project):
    journal = SessionJournal.create(project)
    journal.append("user_input", {"text": "old goal"})
    journal.append("model_request", {"messages": [{"role": "system", "content": "DO NOT REPLAY ME"}]}, call_id="m.1")
    journal.append("model_response", {"content": "arbitrary model instruction", "cost_usd": 0.01}, call_id="m.1", outcome="success")
    journal.append("completed", {"verdict": "PASS"}, outcome="success")
    return journal


def test_valid_resume_and_fork_use_only_host_summary(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("x=1\n")
    journal = completed(project)
    state = record_dialogue_state(journal.session_id, project, goal="inspect x", constraints=["offline only"], decisions=["preserve public API"],
                                  open_questions=["should x change?"], source_paths=["a.py"], mutation_paths=["new.py"],
                                  evidence_refs=[{"ref": "a.py", "scope": str(project)}])
    context = prepare_continuation(journal.session_id, project, "now proceed")
    assert context.query == "now proceed" and context.context["sources"] == state["sources"]
    assert "preserve public API" in context.summary_text
    assert "offline only" in context.summary_text
    assert json.loads(context.summary_text)["evidence_refs"][0]["ref"] == "a.py"
    assert "DO NOT REPLAY ME" not in context.summary_text
    assert "arbitrary model instruction" not in context.summary_text
    assert not context.context["read_evidence"] and not context.context["permissions_granted"]
    fork = fork_session(journal.session_id, project, "forked task")
    assert [event["event_type"] for event in fork.read().events] == ["session_created", "continuation", "user_input", "fork_created", "dialogue_state"]
    assert prepare_continuation(fork.session_id, project, "continue fork").context["kind"] == "fork"
    assert fork.read().pending_call_ids == []
    exported = json.loads(fork.export(tmp_path / "fork.json").read_text())
    assert not exported["replay_supported"]


@pytest.mark.parametrize("kind", ["pending_model", "pending_tool", "unknown_failure", "unknown_cost", "tail"])
def test_resume_and_fork_refuse_unresolved_effects(tmp_path, kind):
    journal = completed(tmp_path)
    record_dialogue_state(journal.session_id, tmp_path, goal="review")
    if kind == "tail":
        with journal.path.open("ab") as handle:
            handle.write(b'{"unfinished":')
    else:
        journal.append("tool_request" if kind == "pending_tool" else "model_request", {}, call_id="later")
        if kind == "unknown_failure":
            journal.append("model_failed", {}, call_id="later", outcome="unknown")
        if kind == "unknown_cost":
            journal.append("model_response", {"cost_usd": None}, call_id="later", outcome="success")
    for operation in (prepare_continuation, fork_session):
        with pytest.raises(StateError, match="unresolved"):
            operation(journal.session_id, tmp_path, "continue")


@pytest.mark.parametrize("indicator", [{"status": "rollback_failed"}, {"side_effects_may_have_occurred": True}])
@pytest.mark.parametrize("location", ["result", "compact_call"])
def test_closed_tool_failure_with_uncertain_effects_blocks_all_continuation(tmp_path, indicator, location):
    journal = completed(tmp_path)
    record_dialogue_state(journal.session_id, tmp_path, goal="review")
    journal.append("tool_request", {"tool": "patch_file"}, call_id="write.1")
    result = {"outcome": "error", "error": "write failed", **indicator}
    payload = {"result": result} if location == "result" else {"call": {"result_summary": result}}
    journal.append("tool_result", payload, call_id="write.1", outcome="error")
    assert journal.read().pending_call_ids == []
    assert journal.read().unresolved_call_ids == ["write.1"]
    with pytest.raises(StateError, match="unresolved"):
        record_dialogue_state(journal.session_id, tmp_path, goal="review")
    for operation in (prepare_continuation, fork_session):
        with pytest.raises(StateError, match="unresolved"):
            operation(journal.session_id, tmp_path, "continue")


@pytest.mark.parametrize("outcome", ["error", "denied"])
def test_read_only_failure_or_denial_without_effects_remains_resumable(tmp_path, outcome):
    journal = completed(tmp_path)
    journal.append("tool_request", {"tool": "read_file"}, call_id="read.1")
    result = {"outcome": outcome, "error": "read failed", "side_effects_may_have_occurred": False}
    journal.append("tool_result", {"result": result, "call": {"result_summary": result}}, call_id="read.1", outcome=outcome)
    assert journal.read().unresolved_call_ids == []
    record_dialogue_state(journal.session_id, tmp_path, goal="review")
    assert prepare_continuation(journal.session_id, tmp_path, "continue").query == "continue"
    assert fork_session(journal.session_id, tmp_path, "continue").read().unresolved_call_ids == []


def test_resume_refuses_stale_mutation_missing_checkpoint_and_wrong_project(tmp_path):
    journal = completed(tmp_path)
    with pytest.raises(StateError, match="checkpoint"):
        prepare_continuation(journal.session_id, tmp_path, "continue")
    record_dialogue_state(journal.session_id, tmp_path, goal="review", mutation_paths=["created.py"])
    (tmp_path / "created.py").write_text("foreign=True")
    with pytest.raises(StateError, match="evidence changed"):
        prepare_continuation(journal.session_id, tmp_path, "continue")
    with pytest.raises(StateError, match="different project"):
        prepare_continuation(journal.session_id, tmp_path / "elsewhere", "continue")


def test_checkpoint_future_version_rejected(tmp_path):
    journal = completed(tmp_path)
    state = record_dialogue_state(journal.session_id, tmp_path, goal="review")
    state["schema_version"] = 2
    journal.append("dialogue_state", state)
    with pytest.raises(StateError, match="unsupported"):
        prepare_continuation(journal.session_id, tmp_path, "continue")


def test_checkpoint_cannot_rehash_changed_observation_as_fresh(tmp_path):
    journal = completed(tmp_path)
    (tmp_path / "a.py").write_text("changed")
    with pytest.raises(StateError, match="hashes changed"):
        record_dialogue_state(journal.session_id, tmp_path, goal="review", source_paths=["a.py"], source_hashes={"a.py": "old_hash"})


def test_continuation_keeps_parent_mutation_evidence_and_updates_source_revision(tmp_path):
    journal = completed(tmp_path)
    (tmp_path / "a.py").write_text("old")
    record_dialogue_state(journal.session_id, tmp_path, goal="review", source_paths=["a.py"], mutation_paths=["gone.py"])
    context = prepare_continuation(journal.session_id, tmp_path, "next")
    sources, mutations = merge_continuation_evidence(context, {"b.py": "b_hash"}, {"a.py": "new_hash"})
    assert sources == {"a.py": "new_hash", "b.py": "b_hash"}
    assert mutations == {"gone.py": None, "a.py": "new_hash"}


def test_session_checkpoint_refuses_concurrent_observation(tmp_path):
    journal = completed(tmp_path)
    sequence = len(journal.read().events)
    journal.append("user_input", {"text": "new steering"})
    with pytest.raises(StateError, match="session changed"):
        journal.append("dialogue_state", {}, expected_sequence=sequence)


@pytest.mark.parametrize("cost", [-1, True, "0.1"])
def test_invalid_closed_response_charge_remains_unresolved(tmp_path, cost):
    journal = completed(tmp_path)
    journal.append("model_request", {}, call_id="invalid")
    journal.append("model_response", {"cost_usd": cost}, call_id="invalid", outcome="success")
    assert "invalid" in journal.read().unresolved_call_ids


def test_preview_apply_restores_edited_created_deleted_and_move(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    for name in ("edited.py", "deleted.py", "move_from.py"):
        (project / name).write_text("original=True\n")
    snap = create_snapshot(project, "before_edits", ["edited.py", "deleted.py", "created.py", "move_from.py", "move_to.py"])
    (project / "edited.py").write_text("changed=True\n")
    (project / "deleted.py").unlink()
    (project / "created.py").write_text("new=True\n")
    (project / "move_from.py").rename(project / "move_to.py")
    preview = preview_rewind(project, snap["snapshot_id"])
    assert len(preview["files"]) == 5 and "changed=True" in preview["diff"]
    result = apply_rewind(project, snap["snapshot_id"], preview["preview_token"])
    assert result["status"] == "applied"
    for name in ("edited.py", "deleted.py", "move_from.py"):
        assert (project / name).read_text() == "original=True\n"
    assert not (project / "created.py").exists() and not (project / "move_to.py").exists()


@pytest.mark.parametrize("changed", ["edited.py", "created.py", "deleted.py"])
def test_stale_preview_never_partially_restores(tmp_path, changed):
    (tmp_path / "edited.py").write_text("old")
    (tmp_path / "deleted.py").write_text("old")
    snap = create_snapshot(tmp_path, "checkpoint", ["edited.py", "created.py", "deleted.py"])
    (tmp_path / "edited.py").write_text("new")
    (tmp_path / "created.py").write_text("new")
    (tmp_path / "deleted.py").unlink()
    preview = preview_rewind(tmp_path, snap["snapshot_id"])
    (tmp_path / changed).write_text("foreign")
    with pytest.raises(SourceConflict, match="stale"):
        apply_rewind(tmp_path, snap["snapshot_id"], preview["preview_token"])
    assert (tmp_path / changed).read_text() == "foreign"
    other = "created.py" if changed == "edited.py" else "edited.py"
    assert (tmp_path / other).read_text() == "new"


def test_interrupted_restore_is_durably_recoverable(tmp_path, monkeypatch):
    import atomic_flow.rewind as rewind

    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("old")
    snap = create_snapshot(tmp_path, "checkpoint", ["a.py", "b.py", "created.py"])
    for name in ("a.py", "b.py", "created.py"):
        (tmp_path / name).write_text("new")
    preview = preview_rewind(tmp_path, snap["snapshot_id"])
    original = rewind._write
    calls = 0

    def crash(project, rel, content, expected, wanted):
        nonlocal calls
        calls += 1
        original(project, rel, content, expected, wanted)
        if calls == 1:
            raise OSError("simulated crash after write before journal update")

    monkeypatch.setattr(rewind, "_write", crash)
    with pytest.raises(OSError, match="simulated"):
        apply_rewind(tmp_path, snap["snapshot_id"], preview["preview_token"])
    journal = next((user_dir() / "rewind").glob("restore_*/restore.json"))
    assert json.loads(journal.read_text())["status"] == "restoring"
    with pytest.raises(StateError, match="interrupted"):
        apply_rewind(tmp_path, snap["snapshot_id"], preview["preview_token"])
    monkeypatch.setattr(rewind, "_write", original)
    # A foreign replacement prevents even safe remaining writes.
    (tmp_path / "b.py").write_text("foreign")
    with pytest.raises(SourceConflict, match="recovery conflict"):
        recover_rewind(tmp_path, journal.parent.name, preview["preview_token"])
    assert (tmp_path / "created.py").exists()
    (tmp_path / "b.py").write_text("new")
    assert recover_rewind(tmp_path, journal.parent.name, preview["preview_token"])["status"] == "applied"
    assert (tmp_path / "a.py").read_text() == (tmp_path / "b.py").read_text() == "old"
    assert not (tmp_path / "created.py").exists()


def test_restore_verifies_earlier_paths_before_marking_applied(tmp_path, monkeypatch):
    import atomic_flow.rewind as rewind

    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("old")
    snap = create_snapshot(tmp_path, "checkpoint", ["a.py", "b.py"])
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("new")
    preview = preview_rewind(tmp_path, snap["snapshot_id"])
    original = rewind._write

    def concurrent_edit(project, rel, content, expected, wanted):
        original(project, rel, content, expected, wanted)
        if rel == "b.py":
            (project / "a.py").write_text("foreign editor version")

    monkeypatch.setattr(rewind, "_write", concurrent_edit)
    with pytest.raises(SourceConflict, match="final verification conflict"):
        apply_rewind(tmp_path, snap["snapshot_id"], preview["preview_token"])
    journal = next((user_dir() / "rewind").glob("restore_*/restore.json"))
    assert json.loads(journal.read_text())["status"] == "restoring"
    assert (tmp_path / "a.py").read_text() == "foreign editor version"
    assert (tmp_path / "b.py").read_text() == "old"
    with pytest.raises(SourceConflict, match="recovery conflict"):
        recover_rewind(tmp_path, journal.parent.name, preview["preview_token"])


def test_preview_omits_large_diff_before_rendering(tmp_path, monkeypatch):
    import atomic_flow.rewind as rewind

    (tmp_path / "large.py").write_text("x=1\n" * 17000)
    snap = create_snapshot(tmp_path, "checkpoint", ["large.py"])
    (tmp_path / "large.py").write_text("x=2\n" * 17000)

    def forbidden_render(*args, **kwargs):
        pytest.fail("oversized file must not reach diff renderer")

    monkeypatch.setattr(rewind.difflib, "unified_diff", forbidden_render)
    preview = preview_rewind(tmp_path, snap["snapshot_id"])
    assert preview["diff_truncated"] and "diff omitted for large.py" in preview["diff"]
    assert len(preview["diff"]) <= rewind.MAX_DIFF_CHARS
    assert "current=" in preview["diff"] and "restore=" in preview["diff"]


def test_preview_caps_diff_output_during_iteration(tmp_path):
    import atomic_flow.rewind as rewind

    (tmp_path / "many.py").write_text("x=1\n" * 6000)
    snap = create_snapshot(tmp_path, "checkpoint", ["many.py"])
    (tmp_path / "many.py").write_text("x=2\n" * 6000)
    preview = preview_rewind(tmp_path, snap["snapshot_id"])
    assert preview["diff_truncated"] and len(preview["diff"]) <= rewind.MAX_DIFF_CHARS


def test_state_root_parent_link_refused_when_supported(tmp_path):
    (tmp_path / "a.py").write_text("source")
    destination = tmp_path / "redirected"
    destination.mkdir()
    user_dir().mkdir(exist_ok=True)
    try:
        (user_dir() / "rewind").symlink_to(destination, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(StateError, match="symlinks/junctions"):
        create_snapshot(tmp_path, "checkpoint", ["a.py"])
    assert not list(destination.iterdir())


@pytest.mark.parametrize("path", ["../outside.py", ".env", "secrets.py", "creds.key", ".git/config"])
def test_snapshot_private_outside_paths_refused(tmp_path, path):
    with pytest.raises((SourceConflict, StateError)):
        create_snapshot(tmp_path, "checkpoint", [path])


def test_snapshot_corruption_and_other_project_refused(tmp_path):
    (tmp_path / "a.py").write_text("original")
    snap = create_snapshot(tmp_path, "checkpoint", ["a.py"])
    with pytest.raises(StateError, match="wrong-project"):
        preview_rewind(tmp_path / "elsewhere", snap["snapshot_id"])
    folder = user_dir() / "rewind" / snap["snapshot_id"]
    (folder / "0").write_text("corrupt")
    with pytest.raises(StateError, match="hash mismatch"):
        preview_rewind(tmp_path, snap["snapshot_id"])


def test_snapshot_total_read_budget_checked_incrementally(tmp_path, monkeypatch):
    import atomic_flow.rewind as rewind

    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("12345")
    snap = create_snapshot(tmp_path, "checkpoint", ["a.py", "b.py"])
    monkeypatch.setattr(rewind, "MAX_SNAPSHOT_BYTES", 8)
    with pytest.raises(StateError, match="backup exceeds limit"):
        preview_rewind(tmp_path, snap["snapshot_id"])


def test_symlink_rejected_when_supported(tmp_path):
    (tmp_path / "a.py").write_text("source")
    try:
        (tmp_path / "linked.py").symlink_to(tmp_path / "a.py")
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(SourceConflict, match="symlink"):
        create_snapshot(tmp_path, "checkpoint", ["linked.py"])


def test_preview_redacts_credentials(tmp_path):
    secret = "sk-" + "S" * 25
    (tmp_path / "a.py").write_text("value='old'\n")
    snap = create_snapshot(tmp_path, "checkpoint", ["a.py"])
    (tmp_path / "a.py").write_text(f"value='{secret}'\n")
    preview = preview_rewind(tmp_path, snap["snapshot_id"])
    assert secret not in preview["diff"] and "[REDACTED]" in preview["diff"]
