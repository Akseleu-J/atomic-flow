"""Command line interface. One small handler per command instead of a 300-line `main`."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

import yaml

from atomic_flow import __version__
from atomic_flow.activation.activate import ActivationIndex, materialize
from atomic_flow.activation.text import has_non_latin_letters
from atomic_flow.agents.undo import UndoStack
from atomic_flow.backends.router import make_backend, resolve_backend_name
from atomic_flow.budget import SpendLedger, prune_runs
from atomic_flow.config import AtomicConfig, is_trusted, load_config, mask_secrets, project_dir, trust_project, user_dir, write_config
from atomic_flow.errors import AtomicFlowError, ConfigurationError, ConfirmationRequired, TreeError
from atomic_flow.kaggle.credentials import (
    credential_status,
    load_credentials,
    prepare_secret_file,
    protect_secret_file,
    sanitize_cli_output,
    secret_file,
)
from atomic_flow.llm.orchestrator import Orchestrator, _Run, choose_mode, scope_ids
from atomic_flow.llm.prompts import PromptRegistry
from atomic_flow.llm.schemas import QueryIntent
from atomic_flow.tree.builder import build_tree, diff_trees, load_tree, refresh_tree, save_tree
from atomic_flow.tree.node import TreeNode


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="atomic", description="Project-aware local LLM gateway")
    p.add_argument("--version", action="version", version=f"atomic_flow {__version__}")
    p.add_argument("--debug", action="store_true", help="Show tracebacks")
    cmd = p.add_subparsers(dest="command", required=True)

    init = cmd.add_parser("init", help="Initialize user or project configuration")
    init.add_argument("--project", action="store_true")
    init.add_argument("--path", default=".")

    trust = cmd.add_parser("trust", help="Allow a project's .atomic_flow/config.yaml to set backends")
    trust.add_argument("path", nargs="?", default=".")

    tree = cmd.add_parser("tree", help="Build and inspect the project tree")
    ts = tree.add_subparsers(dest="tree_action", required=True)
    for action in ("build", "refresh", "status"):
        ts.add_parser(action).add_argument("--path", default=".")
    explain = ts.add_parser("explain")
    explain.add_argument("path", nargs="?")
    explain.add_argument("--project", default=".")
    diff = ts.add_parser("diff", help="Files changed since a git commit")
    diff.add_argument("commit")
    diff.add_argument("--path", default=".")

    ask = cmd.add_parser("ask", help="Ask a question about a project")
    ask.add_argument("query")
    ask.add_argument("--project", default=".")
    ask.add_argument("--mode", choices=["simple", "medium", "hard"])
    ask.add_argument("--no-auto-mode", action="store_true")
    ask.add_argument("--file", help="Restrict retrieval to a file or directory")
    ask.add_argument("--budget", type=int)
    ask.add_argument("--model")
    ask.add_argument("--backend", default="auto")
    ask.add_argument("--role-prompt", action="append", default=[], metavar="ROLE=TEXT|@FILE")
    ask.add_argument("--edit-prompts", action="store_true")
    ask.add_argument("--approve-edits", action="store_true",
                     help="Allow the model to edit files in this request; verification runs after each edit.")
    ask.add_argument("--yes", "-y", action="store_true")
    ask.add_argument("--dry-run", action="store_true")
    ask.add_argument("--no-translate", action="store_true",
                     help="Dry-run: never call the Parser (zero model calls)")
    ask.add_argument("--explain", action="store_true")
    ask.add_argument("--json", action="store_true")

    task_p = cmd.add_parser("task", help="Run a multi-step task")
    task_p.add_argument("query", nargs="?", default="")
    task_p.add_argument("--project", default=".")
    task_p.add_argument("--model")
    task_p.add_argument("--backend", default="auto")
    task_p.add_argument("--yes", "-y", action="store_true")
    task_p.add_argument("--dry-run", action="store_true", help="Show plan only")
    task_p.add_argument("--list", action="store_true", dest="task_list")
    task_p.add_argument("--show", metavar="TASK_ID")
    task_p.add_argument("--resume", metavar="TASK_ID")
    task_p.add_argument("--recover", metavar="TASK_ID", help="Recover an interrupted publication offline; never calls a model")
    task_p.add_argument("--cancel", metavar="TASK_ID")

    undo = cmd.add_parser("undo", help="Restore a previous approved edit")
    undo.add_argument("edit_id", nargs="?", default="last")
    undo.add_argument("--project", default=".")

    doctor = cmd.add_parser("doctor", help="Check local configuration and project readiness")
    doctor.add_argument("--project", default=".")
    doctor.add_argument("--check-backend", action="store_true", help="Send a small live request to the configured model")

    config = cmd.add_parser("config", help="Read or update configuration")
    cs = config.add_subparsers(dest="config_action", required=True)
    get = cs.add_parser("get")
    get.add_argument("key", nargs="?")
    get.add_argument("--project", default=None, help="Project directory (merged view)")
    setter = cs.add_parser("set")
    setter.add_argument("key")
    setter.add_argument("value")
    setter.add_argument("--project", action="store_true")
    cs.add_parser("validate")
    cs.add_parser("edit")

    up = cmd.add_parser("up", help="Run the HTTP API in the foreground")
    up.add_argument("--host", default="127.0.0.1")
    up.add_argument("--port", type=int, default=8000)

    status = cmd.add_parser("status", help="Show local service and project status")
    status.add_argument("--project", default=".")

    runs = cmd.add_parser("runs", help="Inspect saved request artifacts")
    rs = runs.add_subparsers(dest="runs_action", required=True)
    rs.add_parser("list")
    rs.add_parser("show").add_argument("request_id")
    rs.add_parser("prune", help="Delete artifacts older than artifacts.keep_days")

    kaggle = cmd.add_parser("kaggle", help="Manage Kaggle Model Proxy credentials and quota")
    ks = kaggle.add_subparsers(dest="kaggle_action", required=True)
    for name in ("init", "auth"):
        sub = ks.add_parser(name)
        sub.add_argument("--env-file", type=Path, default=None)
        sub.add_argument("--yes", action="store_true")
    for name in ("status", "quota", "models"):
        ks.add_parser(name)

    bench = cmd.add_parser("benchmark", help="Run local evaluations or manage Kaggle benchmark tasks")
    bs = bench.add_subparsers(dest="benchmark_action", required=True)
    bs.add_parser("list")
    bs.add_parser("kaggle-list")
    retrieval = bs.add_parser("retrieval", help="Free: ranking quality only, no model calls")
    retrieval.add_argument("-k", type=int, default=5)
    run = bs.add_parser("run", help="Run the end-to-end suite through the configured LLM backend")
    run.add_argument("--model")
    run.add_argument("--backend", default="auto")
    run.add_argument("--mode", choices=["simple", "medium", "hard"], default="medium")
    run.add_argument("--budget", type=int, default=5000)
    run.add_argument("--limit", type=int)
    run.add_argument("--repeats", type=int, default=1, help="Repeat each case to expose LLM variance")
    run.add_argument("--output", type=Path)
    run.add_argument("--yes", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    push = bs.add_parser("push")
    push.add_argument("task")
    push.add_argument("--file", type=Path)
    push.add_argument("--wait", action="store_true")
    remote = bs.add_parser("run-kaggle")
    remote.add_argument("task")
    remote.add_argument("--model", action="append", default=[])
    remote.add_argument("--wait", action="store_true")
    remote.add_argument("--yes", action="store_true")
    for name in ("status", "log"):
        sub = bs.add_parser(name)
        sub.add_argument("task")
        sub.add_argument("--model", action="append", default=[])
    dl = bs.add_parser("download")
    dl.add_argument("task")
    dl.add_argument("--model", action="append", default=[])
    dl.add_argument("--output", type=Path, default=Path("benchmark-results"))
    return p


# --------------------------------------------------------------------------- helpers

def _set_nested(data: dict[str, Any], key: str, value: Any) -> None:
    pieces = key.split(".")
    for part in pieces[:-1]:
        data = data.setdefault(part, {})
    data[pieces[-1]] = value


def _get_nested(data: dict[str, Any], key: str) -> Any:
    value: Any = data
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(f"No such configuration key: {key}")
        value = value[part]
    return value


def _editor() -> list[str]:
    return shlex.split(os.getenv("EDITOR") or os.getenv("VISUAL") or ("notepad" if os.name == "nt" else "vi"))


def _prompts(args, project: Path, mode: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in args.role_prompt:
        role, sep, value = item.partition("=")
        if not sep or not role or not value:
            raise ValueError(f"Invalid --role-prompt {item!r}; expected ROLE=TEXT or ROLE=@FILE")
        values[role] = Path(value[1:]).expanduser().read_text(encoding="utf-8") if value.startswith("@") else value
    return values


def _make_approval_callback(auto_yes: bool):
    def ask(tool: str, args: dict[str, Any], reason: str) -> bool:
        if auto_yes:
            return True
        print(f"\n[policy] {reason}")
        if tool == "edit_node":
            lines = str(args.get("new_source", "")).splitlines()
            print(f"  edit_node on {args.get('node_id')}")
            print(f"  new source ({len(lines)} lines):")
            for line in lines[:20]:
                print(f"    {line}")
            if len(lines) > 20:
                print("    ...")
        elif tool == "run_command":
            print(f"  command: {args.get('command')}")
        try:
            answer = input("  Approve? [y/N]: ").strip().lower()
        except EOFError:
            return False
        return answer in {"y", "yes"}
    return ask


def _edit_prompts(project: Path, mode: str) -> int:
    registry = PromptRegistry(project)
    prompt_dir = project_dir(project) / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for role in registry.roles_for_mode(mode):
        path = prompt_dir / f"{role}.md"
        if not path.exists():
            path.write_text(registry.get(role), encoding="utf-8")
        paths.append(path)
    subprocess.run([*_editor(), *map(str, paths)], check=False)
    print("Prompts saved under", prompt_dir, "- rerun your query.")           # matches the docs: edit, then exit
    if not is_trusted(project):
        print("This project is not trusted yet. Run `atomic trust .` so these prompts are actually used.")
    return 0


def _run_kaggle_cli(command: list[str], *, timeout: int = 86_400, secret_path: Path | None = None) -> int:
    try:
        proc = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError:
        print("Kaggle CLI is not installed. Install with `pip install -e '.[kaggle]'`.", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("Kaggle CLI command timed out.", file=sys.stderr)
        return 2
    credentials = load_credentials(secret_path, required=False)
    secret = credentials.api_key if credentials else ""
    for stream, text in ((sys.stdout, proc.stdout), (sys.stderr, proc.stderr)):
        clean = sanitize_cli_output(text or "", secret)
        if clean.strip():
            print(clean.rstrip(), file=stream)
    return proc.returncode


# --------------------------------------------------------------------------- command handlers

def cmd_init(args) -> int:
    if args.project:
        root = Path(args.path).expanduser().resolve()
        target = project_dir(root)
        target.mkdir(parents=True, exist_ok=True)
        (target / "focus.yaml").write_text("focus: []\n", encoding="utf-8")
        gitignore = target / ".gitignore"                       # keep generated state out of git, share prompts/config
        if not gitignore.exists():
            gitignore.write_text("tree.json\ntree.json.tmp\n", encoding="utf-8")
        print(f"Initialized project config at {target}\nRun `atomic tree build` to index this project.")
    else:
        target = user_dir()
        target.mkdir(parents=True, exist_ok=True)
        config_path = target / "config.yaml"
        if not config_path.exists():
            write_config(config_path, AtomicConfig().model_dump(mode="json"), merge=False)
        print(f"Initialized user config at {target}")
    return 0


def cmd_trust(args) -> int:
    trust_project(args.path)
    print(f"Trusted: {Path(args.path).expanduser().resolve()} (its .atomic_flow/config.yaml may now set backends).")
    return 0


def cmd_tree(args) -> int:
    root = Path(args.project if args.tree_action == "explain" else args.path).expanduser().resolve()
    config = load_config(root)
    if args.tree_action == "build":
        tree = build_tree(root, config.tree.ignore, respect_gitignore=config.tree.respect_gitignore)
        print(f"Indexed {tree['files']} files, {len(tree['nodes'])} nodes -> {save_tree(root, tree)}")
    elif args.tree_action == "refresh":
        tree, changed = refresh_tree(root, config.tree.ignore, respect_gitignore=config.tree.respect_gitignore)
        s = tree["stats"]
        print(f"{'Updated' if changed else 'Up to date'}: {tree['files']} files, {len(tree['nodes'])} nodes ({s['parsed']} re-parsed, {s['reused']} reused)")
    elif args.tree_action == "status":
        saved = load_tree(root)
        current = build_tree(root, config.tree.ignore, previous=saved, respect_gitignore=config.tree.respect_gitignore)
        d = diff_trees(saved, current)
        print(f"Tree: {len(saved['nodes'])} nodes; added={len(d['added'])} removed={len(d['removed'])} modified={len(d['modified'])}")
        for kind, files in d.items():
            for f in files:
                print(f"  {kind}: {f}")
        return 0 if not any(d.values()) else 1
    elif args.tree_action == "explain":
        for raw in load_tree(root)["nodes"]:
            node = TreeNode.model_validate(raw)
            if args.path and args.path not in node.path and args.path not in node.symbol:
                continue
            indent = "  " * (node.symbol.count(".") + (1 if node.symbol else 0))
            print(f"{indent}{node.label} [{node.kind}, lines {node.start_line}-{node.end_line}, in={node.fan_in} out={node.fan_out}]")
    else:
        changed = subprocess.run(["git", "-C", str(root), "diff", "--name-only", args.commit, "--"], text=True, capture_output=True)
        if changed.returncode:
            raise ValueError(changed.stderr.strip() or "git diff failed")
        print(changed.stdout or "No changed files.")
    return 0


def cmd_ask(args) -> int:
    project = Path(args.project).expanduser().resolve()
    config = load_config(project)
    mode = choose_mode(args.query, config.mode, explicit_mode=args.mode,
                       auto_mode=config.auto_mode and not args.no_auto_mode, file_scope=args.file)
    if args.edit_prompts:
        return _edit_prompts(project, mode)
    prompts = _prompts(args, project, mode)
    registry = PromptRegistry(project, prompts)
    if args.dry_run:
        try:
            backend_name: str | None = resolve_backend_name(config, args.backend)
        except ConfigurationError:
            backend_name = None
        tree, _ = refresh_tree(project, config.tree.ignore) if config.tree.auto_refresh else (load_tree(project), False)
        nodes = [TreeNode.model_validate(n) for n in tree["nodes"]]
        scope = scope_ids(nodes, args.file) if args.file else None
        extra_terms: list[str] | None = None
        if has_non_latin_letters(args.query) and backend_name is not None and not args.no_translate:
            print("[dry-run] non-Latin query detected; running Parser for translation...", file=sys.stderr)
            try:
                parser_run = _Run(make_backend(config, args.backend, args.model), registry,
                                  SpendLedger(0), args.query, None)
                intent = parser_run.invoke("parser", QueryIntent, {
                    "candidate_paths": list(dict.fromkeys(n.path for n in nodes[:30]))
                })
                extra_terms = intent.focus_terms + intent.target_scope
                print(f"[dry-run] parser focus_terms: {intent.focus_terms}", file=sys.stderr)
            except Exception as exc:
                print(f"[dry-run] parser failed ({exc}); using raw query", file=sys.stderr)
        search = ActivationIndex(nodes).search(args.query, extra_terms=extra_terms, limit=80, scope=scope)
        aliases = {n.node_id: f"n{i}" for i, n in enumerate(nodes, 1)}
        context, meta = materialize(project, search.ranked, budget_tokens=args.budget or config.context_budget_tokens,
                                    include_skeleton=mode != "simple", skeleton_nodes=nodes, aliases=aliases)
        model_label = (config.backends[backend_name].model if backend_name in config.backends else "") if backend_name else ""
        print(f"mode={mode}; model={model_label or 'default'}; selected_nodes={len(meta)}; "
              f"retrieval_weak={search.weak}; context_chars={len(context)}")
        for role in registry.roles_for_mode(mode):
            print(f"\n--- prompt: {role} ({registry.sources.get(role) or (registry.get(role) and registry.sources[role])}) ---\n{registry.get(role)}")
        if args.explain:
            for m in meta:
                print(f"{m['alias']} {m['node_id']} score={m['score']:.2f} zoom={m['zoom']} tokens={m['tokens']}")
        print(context)
        return 0
    builtin = [r for r in registry.roles_for_mode(mode) if registry.get(r) and registry.sources[r] == "builtin"]
    if mode == "hard" and builtin and not args.yes:
        if not sys.stdin.isatty():
            raise ConfirmationRequired("Hard mode uses multiple model calls and built-in prompts. Re-run with --yes to acknowledge.")
        print("Hard mode may use several model calls. Some roles use built-in prompts.")
        if input("Proceed? [y/N]: ").strip().lower() not in {"y", "yes"}:
            return 1
    approval_callback = _make_approval_callback(
        args.yes or os.getenv("ATOMIC_FLOW_ALLOW_EDITS", "").lower() in {"1", "true", "yes"}
    )
    result = Orchestrator(config, project, make_backend(config, args.backend, args.model),
                          approval_callback=approval_callback).answer(
        args.query, mode=mode, budget_tokens=args.budget, role_prompts=prompts, file_scope=args.file,
        approve_edits=args.approve_edits)
    if args.json:
        print(result.model_dump_json(indent=2))
        return 0
    print(result.text)
    if result.citations:
        print("\nCitations:")
        for c in result.citations:
            print(f"- {c.node_id}{(' lines ' + c.lines) if c.lines else ''}: {c.what}")
    for warning in result.warnings:
        print(f"! {warning}")
    cost = "n/a" if result.cost_usd is None else f"${result.cost_usd:.4f}{'+' if not result.cost_complete else ''}"
    print(f"\nverdict={result.verdict} (model opinion); grounding={result.grounding:.0%} (deterministic); "
          f"tokens={result.tokens_in}/{result.tokens_out}; cost={cost}; request={result.ledger_id}")
    return 0


def cmd_task(args) -> int:
    from atomic_flow.agents.changeset import ChangeSet
    from atomic_flow.agents.task import list_tasks, load, save
    from atomic_flow.agents.task_loop import TaskRunner

    if args.recover:
        from atomic_flow.agents.control import task_lease
        with task_lease(args.recover):
            task = load(args.recover)
            if not task.changeset_id:
                raise ValueError("Task has no changeset journal to recover.")
            project = Path(task.project).resolve()
            change = ChangeSet.open(project, task.changeset_id)
            if change.state["status"] == "published":
                change.verify_published()
                if task.current_index != len(task.steps):
                    raise ValueError("Published changeset has incomplete task state; review required.")
                task.status = "done"
                task.error = ""
            else:
                change.recover()
                task.status = "failed"
                task.error = "Interrupted publication rolled back; review before starting a new task."
            save_tree(project, build_tree(project))
            save(task)
            _print_task(task, verbose=True)
            return 0
    if args.task_list:
        tasks = list_tasks()
        if not tasks:
            print("no tasks")
            return 0
        for task in tasks:
            done = sum(1 for step in task.steps if step.status == "done")
            print(f"{task.id}\t{task.status}\t{done}/{len(task.steps)}\t${task.budget_spent:.4f}\t{task.query[:60]}")
        return 0

    if args.show:
        try:
            task = load(args.show)
        except FileNotFoundError as exc:
            print(f"atomic: {exc}", file=sys.stderr)
            return 2
        _print_task(task, verbose=True)
        return 0

    if args.cancel:
        try:
            task = load(args.cancel)
        except FileNotFoundError as exc:
            print(f"atomic: {exc}", file=sys.stderr)
            return 2
        from atomic_flow.agents.control import request_cancel
        request_cancel(task.id)
        print(f"cancellation requested: {task.id}")
        return 0

    if args.resume:
        try:
            task = load(args.resume)
        except FileNotFoundError as exc:
            print(f"atomic: {exc}", file=sys.stderr)
            return 2
        if task.status == "done":
            print(f"task {task.id} already done")
            return 0
        config = load_config(task.project)
        backend = make_backend(config, args.backend, args.model)
        runner = TaskRunner(config, Path(task.project), backend,
                           approval_callback=_make_approval_callback(args.yes))
        task = runner.resume(task)
        _print_task(task)
        return 0 if task.status == "done" else 1

    if not args.query:
        print("atomic task: query is required (or use --list/--show/--resume/--cancel)", file=sys.stderr)
        return 2

    project = Path(args.project).expanduser().resolve()
    config = load_config(project)
    try:
        tree = load_tree(project)
    except TreeError:
        tree = build_tree(project, config.tree.ignore, respect_gitignore=config.tree.respect_gitignore)
        save_tree(project, tree)
    nodes = [TreeNode.model_validate(n) for n in tree["nodes"]]
    index = ActivationIndex(nodes)
    search = index.search(args.query, limit=30)
    candidates = [f"{n.path}::{n.symbol}" if n.symbol else n.path for n, _ in search.ranked]
    backend = make_backend(config, args.backend, args.model)

    approval_cb = _make_approval_callback(args.yes)
    runner = TaskRunner(config, project, backend, approval_callback=approval_cb)
    print(f"planning: {args.query}")
    task = runner.prepare(args.query, candidates=candidates, n_files=tree.get("files", len(nodes)))
    if not task.plan_ready:
        _print_task(task)
        return 1
    print(f"\nplan ({len(task.steps)} steps), task={task.id}:")
    for i, step in enumerate(task.steps, 1):
        print(f"  {i}. {step.description}  [{step.verify_with}]")
    if task.rationale:
        print(f"rationale: {task.rationale}")
    print()
    if args.dry_run:
        print(f"plan saved: {task.id}; planning model calls are included in its budget")
        return 0
    if not args.yes:
        if input("Proceed? [y/N]: ").strip().lower() not in {"y", "yes"}:
            print(f"execution not started; plan saved: {task.id}")
            return 1
    task = runner.resume(task)
    _print_task(task)
    return 0 if task.status == "done" else 1


def _print_task(task, verbose: bool = False) -> None:
    print(f"\ntask:   {task.id}")
    print(f"query:  {task.query}")
    print(f"status: {task.status}")
    print(f"budget: ${task.budget_spent:.4f}")
    if task.budget_events:
        unresolved = [event for event in task.budget_events if event.get("status") != "completed"]
        calls = sum(not event.get("not_sent") for event in task.budget_events)
        print(f"model calls: {calls}; unresolved: {len(unresolved)}")
        if unresolved:
            print(f"held estimate: ${sum(event.get('reserved_usd', 0) for event in unresolved):.4f} (cost unknown)")
    if task.rationale and verbose:
        print(f"rationale: {task.rationale}")
    if task.changeset_id:
        print(f"changeset: {task.changeset_id}")
        if verbose:
            print(f"workspace: {user_dir() / 'changesets' / task.changeset_id / 'workspace'}")
    print("steps:")
    for step in task.steps:
        mark = {"done": "✓", "failed": "✗", "running": "→", "pending": "·"}[step.status]
        suffix = f"  -- {step.result_summary[:120]}" if step.result_summary else ""
        print(f"  [{mark}] {step.index + 1}. {step.description}{suffix}")
    if task.error:
        print(f"error: {task.error}")


def cmd_undo(args) -> int:
    project = Path(args.project).expanduser().resolve()
    config = load_config(project)
    record = UndoStack(user_dir() / "edits").undo(project, args.edit_id)
    if record is None:
        print(f"Edit not found or backup unavailable: {args.edit_id}", file=sys.stderr)
        return 1
    save_tree(project, build_tree(project, config.tree.ignore, respect_gitignore=config.tree.respect_gitignore))
    print(f"Restored {record.rel_path} from {record.edit_id}")
    return 0


def cmd_doctor(args) -> int:
    config = load_config(args.project)
    print(f"Python: {sys.version.split()[0]} ({'OK' if sys.version_info >= (3, 11) else 'requires 3.11+'})")
    try:
        tree = load_tree(args.project)
        print(f"Project tree: OK ({len(tree['nodes'])} nodes, {tree['files']} files)")
    except AtomicFlowError as exc:
        print(f"Project tree: NOT READY ({exc})")
    try:
        name = resolve_backend_name(config)
    except ConfigurationError as exc:
        print(f"LLM backend: NOT CONFIGURED ({exc})")
        return 1
    entry = config.backends.get(name)
    if name == "kaggle":
        status = credential_status()
        if not status["configured"] or status["expired"]:
            print(f"Kaggle Model Proxy: {'EXPIRED' if status['configured'] else 'NOT CONFIGURED'} (run `atomic kaggle auth`)")
            return 1
        print(f"Kaggle Model Proxy: configured; expires {status['expires_at'] or 'unknown'}; {status['models_count']} allowed models")
    elif not entry:
        print("LLM backend: NOT CONFIGURED")
        return 1
    elif not args.check_backend:
        ok = bool(entry.resolved_api_key()) or "localhost" in entry.base_url or "127.0.0.1" in entry.base_url
        print(f"LLM backend: {'configured' if ok else 'API key missing'} ({name}: {entry.base_url}, model={entry.model or 'unset'})")
    if args.check_backend:
        try:
            reply = make_backend(config, name).chat([{"role": "user", "content": "Reply with OK."}])
            print(f"LLM backend: {'OK' if reply.content else 'ERROR'} ({name}: live request returned {'content' if reply.content else 'no content'})")
        except AtomicFlowError as exc:
            print(f"LLM backend: ERROR ({exc})")
    else:
        print("Pass --check-backend to send a live model request (billable).")
    print(f"Config: {user_dir() / 'config.yaml'}")
    return 0


def cmd_config(args) -> int:
    if args.config_action == "validate":
        config = load_config()
        print(f"Configuration valid; mode={config.mode}; backends={', '.join(config.backends)}")
    elif args.config_action == "get":
        data = load_config(args.project).model_dump(mode="json")          # the MERGED view, not just one file
        value = data if not args.key else _get_nested(data, args.key)
        print(yaml.safe_dump(mask_secrets(value), sort_keys=False, allow_unicode=True).strip())
    elif args.config_action == "set":
        target = project_dir() / "config.yaml" if args.project else user_dir() / "config.yaml"
        updates: dict[str, Any] = {}
        _set_nested(updates, args.key, yaml.safe_load(args.value))
        write_config(target, updates)                                      # validates BEFORE writing; chmod 600
        print(f"Updated {target}: {args.key}")
        if "api_key" in args.key and not args.key.endswith("_env"):
            print("Tip: prefer backends.<name>.api_key_env (an environment variable name) over storing keys in YAML.")
    else:
        target = user_dir() / "config.yaml"
        if not target.exists():
            write_config(target, AtomicConfig().model_dump(mode="json"), merge=False)
        subprocess.run([*_editor(), str(target)], check=False)
        load_config()                                                      # surface mistakes immediately
    return 0


def cmd_up(args) -> int:
    local = args.host in {"127.0.0.1", "localhost", "::1"}
    if not local and not os.getenv("ATOMIC_FLOW_SERVER_API_KEY"):
        raise ConfigurationError("Binding beyond loopback requires ATOMIC_FLOW_SERVER_API_KEY.")
    if not local and not os.getenv("ATOMIC_FLOW_PROJECT_ROOT"):
        raise ConfigurationError("Binding beyond loopback requires ATOMIC_FLOW_PROJECT_ROOT to limit filesystem access.")
    try:
        import uvicorn
    except ImportError as exc:
        raise ConfigurationError("Install the API extra with `pip install atomic-flow[api]`.") from exc
    print(f"atomic_flow listening on http://{args.host}:{args.port}")
    uvicorn.run("atomic_flow.api.server:create_app", factory=True, host=args.host, port=args.port, reload=False)
    return 0


def cmd_status(args) -> int:
    root = Path(args.project).expanduser().resolve()
    print(f"Project: {root}")
    try:
        print(f"Tree: indexed ({len(load_tree(root)['nodes'])} nodes)")
    except AtomicFlowError:
        print("Tree: not indexed")
    return 0


def cmd_runs(args) -> int:
    root = user_dir() / "runs"
    if args.runs_action == "prune":
        print(f"Removed {prune_runs(load_config().artifacts.keep_days)} old run(s).")
    elif args.runs_action == "list":
        for path in sorted(root.iterdir() if root.exists() else [], key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
                print(f"{path.name}\t{meta.get('mode_used')}\t{meta.get('verdict')}\t{meta.get('model_used')}")
            except (OSError, json.JSONDecodeError):
                print(f"{path.name}\t(failed: see error.json)" if (path / "error.json").exists() else path.name)
    else:
        if not args.request_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Invalid request id.")
        print((root / args.request_id / "meta.json").read_text(encoding="utf-8"))
    return 0


def cmd_kaggle(args) -> int:
    action = args.kaggle_action
    if action == "status":
        status = credential_status()
        if not status["configured"]:
            print("Kaggle Model Proxy: not configured. Run `atomic kaggle auth`.")
            return 1
        state = "expired" if status["expired"] else "expires soon" if status.get("expires_soon") else "ready"
        print(f"Kaggle Model Proxy: {state}; expires_at={status['expires_at'] or 'unknown'}; allowed_models={status['models_count']}; default_model={status['default_model'] or 'unset'}")
        for model in status["models"]:
            print(f"  - {model}")
        return 1 if status["expired"] else 0
    if action == "quota":
        return _run_kaggle_cli(["kaggle", "benchmarks", "quota", "--format", "json"], timeout=60)
    if action == "models":
        return _run_kaggle_cli(["kaggle", "benchmarks", "tasks", "models"], timeout=60)
    path = (args.env_file or secret_file()).expanduser().resolve()
    prepare_secret_file(path)                                  # 0600 BEFORE the key is written
    command = ["kaggle", "benchmarks", action, "--env-file", str(path)]
    if action == "init":
        example = user_dir() / "example_benchmark_task.py"
        command += ["--example-file", str(example)]
    if args.yes:
        command.append("--yes")
    code = _run_kaggle_cli(command, timeout=300, secret_path=path)
    if code == 0:
        protect_secret_file(path)
        status = credential_status(path)
        print(f"Credentials saved to {path}; expiry={status['expires_at'] or 'not reported'}; default_model={status['default_model'] or 'unset'}; secret value suppressed.")
    return code


TASK_FILES = {"atomic-flow-invoice-subtotal": "invoice_subtotal.py", "atomic-flow-refund-window": "refund_window.py",
              "atomic-flow-refund-call-chain": "refund_call_chain.py", "fix-race-condition-in-cpython-threading": "race_condition.py"}


def cmd_benchmark(args) -> int:
    from atomic_flow.benchmarking import benchmark_cases, run_benchmark, run_retrieval_benchmark
    action = args.benchmark_action
    cases = benchmark_cases()
    if action == "list":
        for c in cases:
            print(f"{c['id']}\t{c['mode']}\t{', '.join(c.get('expected_nodes', [])) or '(must abstain)'}\t{c['query']}")
    elif action == "retrieval":
        report = run_retrieval_benchmark(k=args.k)
        print(json.dumps(report["summary"]))
        for r in report["results"]:
            print(f"{r['case_id']}\t{'skipped' if r['skipped'] else ('HIT' if r['hit_all_at_k'] else 'MISS')}\tmrr={r['mrr']}\tweak={r['weak']}")
        return 0 if report["summary"]["hit_all_at_k"] in (1, None) else 1
    elif action == "kaggle-list":
        return _run_kaggle_cli(["kaggle", "benchmarks", "tasks", "list"], timeout=60)
    elif action == "push":
        slug = args.task.strip().lower().replace("_", "-").replace(" ", "-")
        source = (args.file or Path("benchmarks/kaggle_tasks") / TASK_FILES.get(slug, f"{slug}.py")).expanduser().resolve()
        if not source.is_file():
            raise ValueError(f"Benchmark task file not found: {source}")
        return _run_kaggle_cli(["kaggle", "benchmarks", "tasks", "push", args.task, "--file", str(source), *(["--wait"] if args.wait else [])])
    elif action == "run-kaggle":
        if not args.yes:
            raise ValueError("Kaggle will execute model calls. Re-run with --yes to acknowledge the benchmark run.")
        if not args.model:
            raise ValueError("Specify one or more --model slugs for a reproducible Kaggle run.")
        command = ["kaggle", "benchmarks", "tasks", "run", args.task]
        for m in args.model:
            command += ["--model", m]
        return _run_kaggle_cli([*command, *(["--wait"] if args.wait else [])])
    elif action in {"status", "download", "log"}:
        command = ["kaggle", "benchmarks", "tasks", action, args.task]
        for m in args.model:
            command += ["--model", m]
        if action == "download":
            command += ["--output", str(args.output.expanduser().resolve())]
        return _run_kaggle_cli(command)
    else:                                                       # run
        if args.dry_run:
            selected = cases[:args.limit]
            for c in selected:
                print(f"{c['id']}: {c['query']} -> {', '.join(c.get('expected_nodes', [])) or '(abstain)'}")
            print(f"Would run {len(selected)} cases x {args.repeats}; mode={args.mode}; backend={args.backend}; model calls may incur cost.")
            return 0
        if not args.yes:
            raise ValueError("This benchmark sends prompts to a model and can consume quota. Re-run with --yes to acknowledge.")
        report = run_benchmark(load_config(), model=args.model, mode=args.mode, budget_tokens=args.budget, limit=args.limit,
                               output=args.output, backend_name=args.backend, repeats=args.repeats)
        s = report["summary"]
        print(f"{s['passed']}/{s['total']} passed ({s['pass_rate']:.0%}); flaky={s['flaky_cases'] or 'none'}; "
              f"tokens={s['tokens_in']} in/{s['tokens_out']} out; cost={s['cost_usd'] if s['cost_usd'] is not None else 'not reported'}; "
              f"avg latency={s['average_latency_ms']} ms\nReport: {report['output']}")
        return 0 if s["passed"] == s["total"] else 1
    return 0


HANDLERS = {"init": cmd_init, "trust": cmd_trust, "tree": cmd_tree, "ask": cmd_ask, "task": cmd_task, "undo": cmd_undo, "doctor": cmd_doctor,
            "config": cmd_config, "up": cmd_up, "status": cmd_status, "runs": cmd_runs, "kaggle": cmd_kaggle,
            "benchmark": cmd_benchmark}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return HANDLERS[args.command](args)
    except (AtomicFlowError, OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        if args.debug:
            traceback.print_exc()
        print(f"atomic: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
