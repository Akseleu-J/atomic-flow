"""Coupled edits preserve the existing source, policy and check boundaries.

The checker is controlled in these unit tests; real Docker/public journeys remain
separate acceptance evidence.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from atomic_flow.agents import file_transaction
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.agents.loop import execute_tool
from atomic_flow.agents.policy import PolicyEngine, Rule
from atomic_flow.agents.source import SourcePreconditionFailed
from atomic_flow.agents.tools import ROLE_TOOLS, default_registry
from atomic_flow.agents.tools.file_ops import PatchFileArgs, PatchFilesArgs, PatchFilesTool, PatchFileTool
from atomic_flow.agents.tools.read_file import ReadFileArgs, ReadFileTool
from atomic_flow.agents.undo import UndoStack
from atomic_flow.agents.verify import VerificationResult
from atomic_flow.tree.builder import build_tree
from atomic_flow.tree.node import TreeNode

ORIGINAL = {'a.py': 'from b import original\n\ndef call():\n    return original()\n',
            'b.py': 'def original():\n    return 42\n'}
UPDATED = {path: source.replace('original', 'renamed') for path, source in ORIGINAL.items()}


def context(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    for path, content in ORIGINAL.items():
        (root / path).write_bytes(content.encode('utf-8'))
    nodes = {row['node_id']: TreeNode.model_validate(row) for row in build_tree(root)['nodes']}
    ctx = ToolContext(root, nodes, edits_enabled=True, undo_stack=UndoStack(tmp_path / 'undo'),
                      mutation_scope=frozenset(ORIGINAL), enforce_read_before_edit=True)
    read_all(ctx)
    return ctx


def read_all(ctx):
    for path in ORIGINAL:
        ReadFileTool().run(ReadFileArgs(path=path), ctx)


def patches(ctx):
    return [{'path': path, 'expected_hash': ctx.file_hashes[path], 'old_text': ORIGINAL[path], 'new_text': UPDATED[path]}
            for path in ORIGINAL]


def current(ctx):
    return {path: (ctx.project_root / path).read_text(encoding='utf-8') for path in ORIGINAL}


def journals(ctx):
    return [json.loads(path.read_bytes()) for path in ctx.undo_stack.storage_dir.glob('fileedit_*/file-transaction.json')]


def test_coupled_patch_checks_only_after_both_files_are_applied_and_has_one_undo(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    obligation = object()
    ctx.check_obligation = obligation
    observed = []

    def check_combined(root, path, **kwargs):
        assert kwargs['check_obligation'] is obligation
        combined = current(ctx)
        observed.append(combined)
        ok = combined == UPDATED
        return VerificationResult(ok=ok, checks=['syntax', 'pytest'], message='coupled ordinary check', exit_code=0 if ok else 1)

    monkeypatch.setattr('atomic_flow.agents.tools.file_ops.verify_edit', check_combined)
    single = PatchFileTool().run(PatchFileArgs(**patches(ctx)[0]), ctx)
    assert single['status'] == 'rolled_back' and current(ctx) == ORIGINAL
    read_all(ctx)
    before = len(journals(ctx))
    result = PatchFilesTool().run(PatchFilesArgs(patches=patches(ctx)), ctx)
    assert result['status'] == 'applied' and current(ctx) == UPDATED
    assert result['paths'] == list(ORIGINAL) and len(result['changes']) == 2
    assert result['verification']['pytest'] == 'passed'
    assert observed[-2:] == [UPDATED, UPDATED]
    assert len(journals(ctx)) == before + 1
    assert file_transaction.undo(ctx.undo_stack.storage_dir, ctx.project_root, result['edit_id'])
    assert current(ctx) == ORIGINAL


@pytest.mark.parametrize('failure', ['stale', 'scope', 'unread', 'fragment', 'range'])
def test_every_second_file_precondition_is_checked_before_any_source_write(tmp_path, failure):
    ctx = context(tmp_path)
    arguments = patches(ctx)
    if failure == 'stale':
        arguments[1]['expected_hash'] = '0' * 40
    elif failure == 'scope':
        ctx.mutation_scope = frozenset({'a.py'})
    elif failure == 'unread':
        ctx.reads = [proof for proof in ctx.reads if proof['path'] != 'b.py']
    elif failure == 'fragment':
        arguments[1]['old_text'] = 'absent fragment'
    else:
        arguments[1].update(start_line=2, end_line=2)
    with pytest.raises(SourcePreconditionFailed):
        PatchFilesTool().run(PatchFilesArgs(patches=arguments), ctx)
    assert current(ctx) == ORIGINAL and journals(ctx) == []


def test_check_failure_rolls_back_the_whole_source_set(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    observations = []

    def fail(root, path, **kwargs):
        observations.append(current(ctx))
        return VerificationResult(ok=False, checks=['syntax', 'pytest'], message='ordinary failure', exit_code=1)

    monkeypatch.setattr('atomic_flow.agents.tools.file_ops.verify_edit', fail)
    result = PatchFilesTool().run(PatchFilesArgs(patches=patches(ctx)), ctx)
    assert observations == [UPDATED]
    assert result['status'] == 'rolled_back' and result['verification']['pytest'] == 'failed'
    assert current(ctx) == ORIGINAL
    assert journals(ctx)[0]['status'] == 'undone'


def test_cancel_between_existing_transaction_writes_restores_all_source(tmp_path):
    ctx = context(tmp_path)

    def cancel_between_writes():
        observed = current(ctx)
        if observed['a.py'] == UPDATED['a.py'] and observed['b.py'] == ORIGINAL['b.py']:
            raise TaskCancelled('stop between transaction writes')

    ctx.cancel_check = cancel_between_writes
    with pytest.raises(TaskCancelled):
        PatchFilesTool().run(PatchFilesArgs(patches=patches(ctx)), ctx)
    assert current(ctx) == ORIGINAL and journals(ctx)[0]['status'] == 'undone'


def test_interrupted_batch_keeps_existing_recoverable_multi_path_journal(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    original_write = file_transaction._write

    def crash_after_first_write(root, path, data, expected):
        original_write(root, path, data, expected)
        raise KeyboardInterrupt('simulated process interruption')

    monkeypatch.setattr(file_transaction, '_write', crash_after_first_write)
    with pytest.raises(KeyboardInterrupt):
        PatchFilesTool().run(PatchFilesArgs(patches=patches(ctx)), ctx)
    state = journals(ctx)[0]
    assert state['status'] == 'writing' and set(state['changes']) == set(ORIGINAL)
    monkeypatch.setattr(file_transaction, '_write', original_write)
    assert file_transaction.undo(ctx.undo_stack.storage_dir, ctx.project_root, state['edit_id'], interrupted=True)
    assert current(ctx) == ORIGINAL and journals(ctx)[0]['status'] == 'undone'


def test_batch_schema_bounds_unique_paths_and_default_registry():
    patch = {'path': 'a.py', 'expected_hash': 'a' * 40, 'old_text': 'old', 'new_text': 'new'}
    for rows in ([], [patch, patch], [{**patch, 'path': f'{index}.py'} for index in range(17)]):
        with pytest.raises(ValidationError):
            PatchFilesArgs(patches=rows)
    assert default_registry().specification('patch_files').args_schema is PatchFilesArgs
    assert 'patch_files' in ROLE_TOOLS['answerer']


def test_all_batch_paths_take_part_in_policy_allow_deny_and_line_limits():
    args = {'patches': [{'path': 'a.py', 'new_text': 'one\ntwo'}, {'path': 'b.py', 'new_text': 'three\nfour'}]}
    allow = PolicyEngine([Rule('patch_files', 'auto', path_matches='a.py')])
    assert allow.decide('patch_files', args, project_trusted=True).action == 'ask'
    deny = PolicyEngine([Rule('patch_files', 'auto'), Rule('patch_files', 'forbid', path_matches='b.py')])
    assert deny.decide('patch_files', args, project_trusted=True).action == 'forbid'
    small = PolicyEngine([Rule('patch_files', 'auto', lines_lt=3)])
    assert small.decide('patch_files', args, project_trusted=True).action == 'ask'


@pytest.mark.parametrize('count', [0, 17])
def test_offline_policy_refuses_unbounded_or_empty_batch_without_expanding_items(count):
    engine = PolicyEngine([Rule('patch_files', 'auto')])
    args = {'patches': [{'path': 'a.py', 'new_text': 'bounded'}] * count}
    explanation = engine.explain('patch_files', args, project_trusted=True)
    assert explanation['action'] == 'ask' and 'batch_items' not in explanation


def test_dispatcher_denies_second_path_before_approval_or_invocation(tmp_path):
    ctx = context(tmp_path)
    ctx.mutation_scope = frozenset({'a.py'})
    approvals = []
    ctx.approval_callback = lambda *args: approvals.append(args) or True
    result, record = execute_tool({'tool': 'patch_files', 'args': {'patches': patches(ctx)}}, default_registry(), ctx,
                                  allowed_tools=['patch_files'], role='answerer')
    assert result['outcome'] == 'denied' and record['result_summary']['outcome'] == 'denied'
    assert approvals == [] and current(ctx) == ORIGINAL and journals(ctx) == []


def test_batch_cannot_bypass_an_existing_single_file_policy_denial(tmp_path):
    ctx = context(tmp_path)
    ctx.policy = PolicyEngine([Rule('patch_files', 'auto'), Rule('patch_file', 'forbid', path_matches='b.py')])
    result, record = execute_tool({'tool': 'patch_files', 'args': {'patches': patches(ctx)}}, default_registry(), ctx,
                                  allowed_tools=['patch_files'], role='answerer')
    assert result['outcome'] == 'denied' and record['policy_source'] == 'user'
    assert current(ctx) == ORIGINAL and journals(ctx) == []
    explanation = ctx.policy.explain('patch_files', {'patches': patches(ctx)}, project_trusted=ctx.trusted)
    assert explanation['action'] == 'forbid'
    assert explanation['batch_items'][1]['action'] == 'forbid'


@pytest.mark.parametrize('tool', ['patch_file', 'patch_files'])
def test_existing_replacement_line_limit_and_explicit_ask_survive_batch_allow(tool):
    policy = PolicyEngine(rules=[Rule('patch_files', 'auto'), Rule('patch_file', 'auto', lines_lt=20),
                                 Rule('patch_file', 'ask')])
    patch = {'path': 'a.py', 'new_text': 'changed\n' * 25}
    arguments = {'patches': [patch]} if tool == 'patch_files' else patch
    assert policy.decide(tool, arguments, project_trusted=True).action == 'ask'
    assert policy.explain(tool, arguments, project_trusted=True)['action'] == 'ask'
    patch['new_text'] = 'small\n'
    assert policy.decide(tool, arguments, project_trusted=True).action == 'auto'
