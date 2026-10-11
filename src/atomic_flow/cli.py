"""Command line interface for the local daemon, project tree, and queries."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from atomic_flow import __version__
from atomic_flow.activation.activate import activate, materialize
from atomic_flow.backends.router import make_backend
from atomic_flow.config import AtomicConfig, load_config, project_dir, user_dir, write_config
from atomic_flow.errors import AtomicFlowError, ConfigurationError
from atomic_flow.llm.orchestrator import Orchestrator, choose_mode
from atomic_flow.llm.prompts import PromptRegistry
from atomic_flow.tree.builder import build_tree, load_tree, save_tree
from atomic_flow.tree.node import TreeNode
from atomic_flow.kaggle.credentials import (SECRET_FILE, credential_status, load_credentials,
                                            protect_secret_file, sanitize_cli_output)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atomic", description="Project-aware local LLM gateway")
    parser.add_argument("--version", action="version", version=f"atomic_flow {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Initialize user or project configuration")
    init.add_argument("--project", action="store_true")
    init.add_argument("--path", default=".")

    tree = commands.add_parser("tree", help="Build and inspect the project tree")
    tree_sub = tree.add_subparsers(dest="tree_action", required=True)
    for action in ("build", "refresh"):
        sub = tree_sub.add_parser(action)
        sub.add_argument("--path", default=".")
    status = tree_sub.add_parser("status")
    status.add_argument("--path", default=".")
    explain = tree_sub.add_parser("explain")
    explain.add_argument("path", nargs="?")
    explain.add_argument("--project", default=".")
    diff = tree_sub.add_parser("diff")
    diff.add_argument("commit")
    diff.add_argument("--path", default=".")

    ask = commands.add_parser("ask", help="Ask a question about a project")
    ask.add_argument("query")
    ask.add_argument("--project", default=".")
    ask.add_argument("--mode", choices=["simple", "medium", "hard"])
    ask.add_argument("--no-auto-mode", action="store_true")
    ask.add_argument("--file")
    ask.add_argument("--budget", type=int)
    ask.add_argument("--model")
    ask.add_argument("--backend", default="auto")
    ask.add_argument("--role-prompt", action="append", default=[], metavar="ROLE=TEXT|@FILE")
    ask.add_argument("--edit-prompts", action="store_true")
    ask.add_argument("--yes", "-y", action="store_true")
    ask.add_argument("--dry-run", action="store_true")
    ask.add_argument("--explain", action="store_true")
    ask.add_argument("--json", action="store_true")

    doctor = commands.add_parser("doctor", help="Check local configuration and project readiness")
    doctor.add_argument("--project", default=".")
    doctor.add_argument("--check-backend", action="store_true", help="Send a small live request to the configured model")

    config = commands.add_parser("config", help="Read or update configuration")
    config_sub = config.add_subparsers(dest="config_action", required=True)
    for action in ("get", "set"):
        sub = config_sub.add_parser(action)
        sub.add_argument("key")
        if action == "set":
            sub.add_argument("value")
        sub.add_argument("--project", action="store_true")
    config_sub.add_parser("validate")
    config_sub.add_parser("edit")

    up = commands.add_parser("up", help="Run the HTTP API in the foreground")
    up.add_argument("--host", default="127.0.0.1")
    up.add_argument("--port", type=int, default=8000)

    status = commands.add_parser("status", help="Show local service and project status")
    status.add_argument("--project", default=".")

    runs = commands.add_parser("runs", help="Inspect saved request artifacts")
    runs_sub = runs.add_subparsers(dest="runs_action", required=True)
    runs_sub.add_parser("list")
    show = runs_sub.add_parser("show")
    show.add_argument("request_id")

    kaggle = commands.add_parser("kaggle", help="Manage Kaggle Model Proxy credentials and quota")
    kaggle_sub = kaggle.add_subparsers(dest="kaggle_action", required=True)
    kaggle_init = kaggle_sub.add_parser("init", help="Initialize a local Kaggle benchmark environment and fetch credentials")
    kaggle_init.add_argument("--env-file", type=Path, default=SECRET_FILE)
    kaggle_init.add_argument("--yes", action="store_true")
    auth = kaggle_sub.add_parser("auth", help="Fetch short-lived Kaggle Model Proxy credentials")
    auth.add_argument("--env-file", type=Path, default=SECRET_FILE)
    auth.add_argument("--yes", action="store_true")
    kaggle_sub.add_parser("status", help="Show credential expiry and model access without revealing secrets")
    kaggle_sub.add_parser("quota", help="Show Kaggle Model Proxy inference quota")
    kaggle_sub.add_parser("models", help="List models supported by Kaggle Benchmarks")

    benchmark = commands.add_parser("benchmark", help="Run local evaluations or manage Kaggle benchmark tasks")
    benchmark_sub = benchmark.add_subparsers(dest="benchmark_action", required=True)
    benchmark_sub.add_parser("list", help="List the built-in local evaluation cases")
    benchmark_sub.add_parser("kaggle-list", help="List benchmark tasks uploaded to Kaggle")
    run_bench = benchmark_sub.add_parser("run", help="Run the local end-to-end suite through the configured LLM backend")
    run_bench.add_argument("--model")
    run_bench.add_argument("--backend", default="auto")
    run_bench.add_argument("--mode", choices=["simple", "medium", "hard"], default="medium")
    run_bench.add_argument("--budget", type=int, default=5000)
    run_bench.add_argument("--limit", type=int)
    run_bench.add_argument("--output", type=Path)
    run_bench.add_argument("--yes", action="store_true", help="Acknowledge model calls and possible inference cost")
    run_bench.add_argument("--dry-run", action="store_true")
    push = benchmark_sub.add_parser("push", help="Upload a local Kaggle Benchmarks task")
    push.add_argument("task")
    push.add_argument("--file", type=Path, help="Task source file; defaults to benchmarks/kaggle_tasks/<task-slug>.py")
    push.add_argument("--wait", action="store_true")
    remote_run = benchmark_sub.add_parser("run-kaggle", help="Schedule a Kaggle-hosted benchmark run")
    remote_run.add_argument("task")
    remote_run.add_argument("--model", action="append", default=[])
    remote_run.add_argument("--wait", action="store_true")
    remote_run.add_argument("--yes", action="store_true", help="Acknowledge that Kaggle will run model evaluations")
    for action in ("status", "log"):
        sub = benchmark_sub.add_parser(action)
        sub.add_argument("task")
        sub.add_argument("--model", action="append", default=[])
    download = benchmark_sub.add_parser("download")
    download.add_argument("task")
    download.add_argument("--model", action="append", default=[])
    download.add_argument("--output", type=Path, default=Path("benchmark-results"))

    return parser


def _set_nested(data: dict[str, Any], key: str, value: Any) -> None:
    pieces = key.split(".")
    for part in pieces[:-1]:
        data = data.setdefault(part, {})
    data[pieces[-1]] = value


def _get_nested(data: dict[str, Any], key: str) -> Any:
    value: Any = data
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(key)
        value = value[part]
    return value


def _prompts(args, project: Path, mode: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in args.role_prompt:
        role, sep, value = item.partition("=")
        if not sep or not role or not value:
            raise ValueError(f"Invalid --role-prompt {item!r}; expected ROLE=TEXT or ROLE=@FILE")
        if value.startswith("@"):
            values[role] = Path(value[1:]).expanduser().read_text(encoding="utf-8")
        else:
            values[role] = value
    if args.edit_prompts:
        role_list = PromptRegistry.roles_for_mode(mode)
        prompt_dir = project_dir(project) / "prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        registry = PromptRegistry(project)
        paths = []
        for role in role_list:
            path = prompt_dir / f"{role}.md"
            if not path.exists():
                path.write_text(registry.get(role), encoding="utf-8")
            paths.append(path)
        editor = os.getenv("EDITOR") or os.getenv("VISUAL") or ("notepad" if os.name == "nt" else "vi")
        if os.name == "nt" and Path(shlex.split(editor)[0]).name.lower() == "notepad":
            for path in paths:
                subprocess.run(shlex.split(editor) + [str(path)], check=False)
        else:
            subprocess.run(shlex.split(editor) + [str(path) for path in paths], check=False)
        for path in paths:
            values.setdefault(path.stem, path.read_text(encoding="utf-8"))
    return values


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
    out = sanitize_cli_output(proc.stdout or "", secret)
    err = sanitize_cli_output(proc.stderr or "", secret)
    if out.strip():
        print(out.rstrip())
    if err.strip():
        print(err.rstrip(), file=sys.stderr)
    return proc.returncode


def _run_kaggle_command(args) -> int:
    if args.kaggle_action == "status":
        status = credential_status()
        if not status["configured"]:
            print("Kaggle Model Proxy: not configured. Run `atomic kaggle auth`.")
            return 1
        expiry = status["expires_at"] or "unknown"
        state = "expired" if status["expired"] else "expires soon" if status.get("expires_soon") else "ready"
        print(f"Kaggle Model Proxy: {state}; expires_at={expiry}; allowed_models={status['models_count']}; default_model={status['default_model'] or 'unset'}")
        for model_name in status.get("models", []):
            print(f"  - {model_name}")
        return 1 if status["expired"] else 0
    if args.kaggle_action == "auth":
        path = args.env_file.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        command = ["kaggle", "benchmarks", "auth", "--env-file", str(path)]
        if args.yes:
            command.append("--yes")
        result = _run_kaggle_cli(command, timeout=300, secret_path=path)
        if result == 0:
            protect_secret_file(path)
            status = credential_status(path)
            print(f"Credentials saved to {path}; expiry={status['expires_at'] or 'not reported'}; secret value suppressed.")
        return result
    if args.kaggle_action == "init":
        path = args.env_file.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        example = user_dir() / "example_benchmark_task.py"
        example.parent.mkdir(parents=True, exist_ok=True)
        command = ["kaggle", "benchmarks", "init", "--env-file", str(path), "--example-file", str(example)]
        if args.yes:
            command.append("--yes")
        result = _run_kaggle_cli(command, timeout=300, secret_path=path)
        if result == 0:
            protect_secret_file(path)
            status = credential_status(path)
            print(f"Kaggle environment initialized; credentials={path}; default_model={status['default_model'] or 'unset'}; available_models={status['models_count']}.")
        return result
    if args.kaggle_action == "quota":
        return _run_kaggle_cli(["kaggle", "benchmarks", "quota", "--format", "json"], timeout=60)
    if args.kaggle_action == "models":
        return _run_kaggle_cli(["kaggle", "benchmarks", "tasks", "models"], timeout=60)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            if args.project:
                root = Path(args.path).expanduser().resolve()
                root.mkdir(parents=True, exist_ok=True)
                target = project_dir(root)
                target.mkdir(parents=True, exist_ok=True)
                (target / "focus.yaml").write_text("focus: []\n", encoding="utf-8")
                print(f"Initialized project config at {target}")
                print("Run `atomic tree build` to index this project.")
            else:
                target = user_dir()
                target.mkdir(parents=True, exist_ok=True)
                config_path = target / "config.yaml"
                if not config_path.exists():
                    config_path.write_text(yaml.safe_dump(AtomicConfig().model_dump(mode="json"), sort_keys=False), encoding="utf-8")
                print(f"Initialized user config at {target}")
            return 0

        if args.command == "tree":
            root_value = args.project if args.tree_action == "explain" else args.path
            root = Path(root_value).expanduser().resolve()
            if args.tree_action in {"build", "refresh"}:
                config = load_config(root)
                tree = build_tree(root, config.tree.get("ignore"))
                target = save_tree(root, tree)
                print(f"Indexed {tree['files']} files, {len(tree['nodes'])} nodes → {target}")
            elif args.tree_action == "status":
                saved = load_tree(root)
                current = build_tree(root, load_config(root).tree.get("ignore"))
                saved_ids = {n["node_id"] for n in saved["nodes"]}
                current_ids = {n["node_id"] for n in current["nodes"]}
                print(f"Tree: {len(saved_ids)} nodes; changed: {len(saved_ids ^ current_ids)} nodes")
            elif args.tree_action == "explain":
                saved = load_tree(root)
                for raw in saved["nodes"]:
                    node = TreeNode.model_validate(raw)
                    if args.path and args.path not in node.path and args.path not in node.symbol:
                        continue
                    indent = "  " * (node.symbol.count(".") + (1 if node.symbol else 0))
                    label = f"{node.path}::{node.symbol}" if node.symbol else node.path
                    print(f"{indent}{label} [{node.kind}, lines {node.start_line}-{node.end_line}]")
            else:
                import subprocess as sp
                changed = sp.run(["git", "-C", str(root), "diff", "--name-only", args.commit, "--"], text=True, capture_output=True)
                if changed.returncode:
                    raise ValueError(changed.stderr.strip() or "git diff failed")
                print(changed.stdout or "No changed files.")
            return 0

        if args.command == "ask":
            project = Path(args.project).expanduser().resolve()
            config = load_config(project)
            mode = choose_mode(args.query, config.mode, explicit_mode=args.mode,
                               auto_mode=config.auto_mode and not args.no_auto_mode, file_scope=args.file)
            resolved_backend_name = (config.default_backend if config.default_backend != "auto" else "openai-compatible") if args.backend == "auto" else args.backend
            backend_config = config.backends.get(resolved_backend_name)
            if not backend_config:
                raise ConfigurationError(f"Backend is not configured: {args.backend}")
            if args.model:
                backend_config = backend_config.model_copy(update={"model": args.model})
            prompts = _prompts(args, project, mode)
            registry = PromptRegistry(project, prompts)
            if args.dry_run:
                nodes = [TreeNode.model_validate(item) for item in load_tree(project)["nodes"]]
                candidates = activate(args.query, nodes, limit=min(80, len(nodes)))
                if args.file:
                    candidates = [(n, s) for n, s in candidates if n.path == args.file or n.path.endswith("/" + args.file)]
                context, meta = materialize(project, candidates, budget_tokens=args.budget or config.context_budget_tokens, include_skeleton=mode != "simple")
                print(f"mode={mode}; model={backend_config.model}; selected_nodes={len(meta)}; context_chars={len(context)}")
                for role in PromptRegistry.roles_for_mode(mode):
                    prompt_text = registry.get(role)
                    print(f"\n--- prompt: {role} ({registry.sources[role]}) ---\n{prompt_text}")
                if args.explain:
                    for node in meta:
                        print(f"{node['node_id']} score={node['score']:.2f} zoom={node['zoom']}")
                print(context)
                return 0
            default_roles = [r for r in registry.roles_for_mode(mode) if registry.get(r) and registry.sources[r] == "builtin"]
            if mode == "hard" and default_roles and not args.yes and not sys.stdin.isatty():
                from atomic_flow.errors import ConfirmationRequired
                raise ConfirmationRequired("Hard mode uses multiple model calls and built-in prompts. Re-run with --yes to acknowledge.")
            if mode == "hard" and default_roles and not args.yes:
                print("Hard mode may use several model calls. Some roles use built-in prompts.")
                if input("Proceed? [y/N]: ").strip().lower() not in {"y", "yes"}:
                    return 1
            result = Orchestrator(config, project, make_backend(config, args.backend, args.model)).answer(args.query, mode=mode, budget_tokens=args.budget, role_prompts=prompts, file_scope=args.file)
            if args.json:
                print(result.model_dump_json(indent=2))
            else:
                print(result.text)
                if result.citations:
                    print("\nCitations:")
                    ids = {item["node_id"]: item for item in load_tree(project)["nodes"]}
                    for citation in result.citations:
                        node = ids.get(citation.node_id)
                        print(f"- {node['path']}::{node['symbol'] if node else citation.node_id}: {citation.what}")
                print(f"\nverdict={result.verdict}; coverage={result.citation_coverage:.0%}; request={result.ledger_id}; artifacts={result.artifacts_path}")
            return 0

        if args.command == "doctor":
            config = load_config(args.project)
            print(f"Python: {sys.version.split()[0]} ({'OK' if sys.version_info >= (3, 11) else 'requires 3.11+'})")
            try:
                tree = load_tree(args.project)
                print(f"Project tree: OK ({len(tree['nodes'])} nodes, {tree['files']} files)")
            except AtomicFlowError as exc:
                print(f"Project tree: NOT READY ({exc})")
            backend_name = config.default_backend if config.default_backend != "auto" else "openai-compatible"
            if backend_name == "kaggle":
                status = credential_status()
                if not status["configured"]:
                    print("Kaggle Model Proxy: NOT CONFIGURED (run `atomic kaggle auth`)")
                    return 1
                if status["expired"]:
                    print("Kaggle Model Proxy: EXPIRED (run `atomic kaggle auth`)")
                    return 1
                print(f"Kaggle Model Proxy: configured; expires {status['expires_at'] or 'unknown'}; {status['models_count']} allowed models")
                if args.check_backend:
                    try:
                        response = make_backend(config, backend_name).chat([{"role": "user", "content": "Reply with OK."}])
                        print(f"LLM backend: {'OK' if response.content else 'ERROR'} (kaggle: live request returned {'content' if response.content else 'no content'})")
                    except AtomicFlowError as exc:
                        print(f"LLM backend: ERROR ({exc})")
                else:
                    print("Pass --check-backend to send a live model request.")
                print(f"Config: {user_dir() / 'config.yaml'}")
                return 0
            backend = config.backends.get(backend_name)
            if not backend:
                print("LLM backend: NOT CONFIGURED")
            elif args.check_backend:
                try:
                    response = make_backend(config, backend_name).chat([{"role": "user", "content": "Reply with OK."}])
                    print(f"LLM backend: {'OK' if response.content else 'ERROR'} ({backend_name}: live request returned {'content' if response.content else 'no content'})")
                except AtomicFlowError as exc:
                    print(f"LLM backend: ERROR ({exc})")
            else:
                configured = bool(backend.api_key) or "localhost" in backend.base_url or "127.0.0.1" in backend.base_url
                print(f"LLM backend: {'configured' if configured else 'API key missing'} ({backend_name}: {backend.base_url}, model={backend.model})")
                print("Pass --check-backend to send a live model request.")
            print(f"Config: {user_dir() / 'config.yaml'}")
            return 0

        if args.command == "kaggle":
            return _run_kaggle_command(args)

        if args.command == "benchmark":
            from atomic_flow.benchmarking import benchmark_cases, run_benchmark
            cases = benchmark_cases()
            if args.benchmark_action == "list":
                for case in cases:
                    print(f"{case['id']}\t{case['mode']}\t{case['path']}::{case['symbol']}\t{case['query']}")
                return 0
            if args.benchmark_action == "kaggle-list":
                return _run_kaggle_cli(["kaggle", "benchmarks", "tasks", "list"], timeout=60)
            if args.benchmark_action == "push":
                task_slug = args.task.strip().lower().replace("_", "-").replace(" ", "-")
                default_files = {
                    "atomic-flow-invoice-subtotal": "invoice_subtotal.py",
                    "atomic-flow-refund-window": "refund_window.py",
                    "atomic-flow-refund-call-chain": "refund_call_chain.py",
                    "fix-race-condition-in-cpython-threading": "race_condition.py",
                }
                default_file = Path("benchmarks/kaggle_tasks") / default_files.get(task_slug, f"{task_slug}.py")
                source = (args.file or default_file).expanduser().resolve()
                if not source.is_file():
                    raise ValueError(f"Benchmark task file not found: {source}")
                command = ["kaggle", "benchmarks", "tasks", "push", args.task, "--file", str(source)]
                if args.wait:
                    command.append("--wait")
                return _run_kaggle_cli(command)
            if args.benchmark_action == "run-kaggle":
                if not args.yes:
                    raise ValueError("Kaggle will execute model calls. Re-run with --yes to acknowledge the benchmark run.")
                if not args.model:
                    raise ValueError("Specify one or more --model slugs for a reproducible Kaggle run.")
                command = ["kaggle", "benchmarks", "tasks", "run", args.task]
                for model_name in args.model:
                    command.extend(["--model", model_name])
                if args.wait:
                    command.append("--wait")
                return _run_kaggle_cli(command)
            if args.benchmark_action in {"status", "download", "log"}:
                command = ["kaggle", "benchmarks", "tasks", args.benchmark_action, args.task]
                for model_name in getattr(args, "model", []):
                    command.extend(["--model", model_name])
                if args.benchmark_action == "download":
                    command.extend(["--output", str(args.output.expanduser().resolve())])
                return _run_kaggle_cli(command)
            if args.dry_run:
                selected = cases[:args.limit]
                for case in selected:
                    print(f"{case['id']}: {case['query']} → {case['path']}::{case['symbol']}")
                print(f"Would run {len(selected)} cases; mode={args.mode}; backend={args.backend}; model calls may incur provider cost.")
                return 0
            if not args.yes:
                raise ValueError("This benchmark sends prompts to a model and can consume Kaggle quota. Re-run with --yes to acknowledge.")
            report = run_benchmark(load_config(), model=args.model, mode=args.mode, budget_tokens=args.budget,
                                   limit=args.limit, output=args.output, backend_name=args.backend)
            summary = report["summary"]
            print(f"{summary['passed']}/{summary['total']} passed ({summary['pass_rate']:.0%}); "
                  f"tokens={summary['tokens_in']} in/{summary['tokens_out']} out; "
                  f"cost={summary['cost_usd'] if summary['cost_usd'] is not None else 'not reported'}; "
                  f"avg latency={summary['average_latency_ms']} ms")
            print(f"Report: {report['output']}")
            return 0 if summary["passed"] == summary["total"] else 1

        if args.command == "config":
            if args.config_action == "validate":
                config = load_config()
                print(f"Configuration valid; mode={config.mode}; backends={', '.join(config.backends)}")
                return 0
            target = project_dir() / "config.yaml" if args.project else user_dir() / "config.yaml"
            data = yaml.safe_load(target.read_text(encoding="utf-8")) if target.exists() else AtomicConfig().model_dump(mode="json")
            if args.config_action == "get":
                value = _get_nested(data, args.key)
                if args.key.endswith("api_key") and value:
                    value = "********"
                print(yaml.safe_dump(value, sort_keys=False, allow_unicode=True).strip())
            elif args.config_action == "set":
                value = yaml.safe_load(args.value)
                updates: dict[str, Any] = {}
                _set_nested(updates, args.key, value)
                write_config(target, updates)
                load_config(Path.cwd() if args.project else None)
                print(f"Updated {target}: {args.key}")
            else:
                editor = os.getenv("EDITOR") or ("notepad" if os.name == "nt" else "vi")
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.write_text(yaml.safe_dump(AtomicConfig().model_dump(mode="json"), sort_keys=False), encoding="utf-8")
                subprocess.run(shlex.split(editor) + [str(target)], check=False)
            return 0

        if args.command == "up":
            if args.host not in {"127.0.0.1", "localhost", "::1"} and not os.getenv("ATOMIC_FLOW_SERVER_API_KEY"):
                raise ConfigurationError("Binding beyond loopback requires ATOMIC_FLOW_SERVER_API_KEY. Set the key before starting the API.")
            if args.host not in {"127.0.0.1", "localhost", "::1"} and not os.getenv("ATOMIC_FLOW_PROJECT_ROOT"):
                raise ConfigurationError("Binding beyond loopback requires ATOMIC_FLOW_PROJECT_ROOT to limit filesystem access.")
            try:
                import uvicorn
            except ImportError as exc:
                raise ConfigurationError("Install the API extra with `pip install atomic-flow[api]`.") from exc
            print(f"atomic_flow listening on http://{args.host}:{args.port}")
            uvicorn.run("atomic_flow.api.server:app", host=args.host, port=args.port, reload=False)
            return 0

        if args.command == "status":
            root = Path(args.project).expanduser().resolve()
            print(f"Project: {root}")
            try:
                tree = load_tree(root)
                print(f"Tree: indexed ({len(tree['nodes'])} nodes)")
            except AtomicFlowError:
                print("Tree: not indexed")
            print(f"API: configure `atomic up` to serve on 127.0.0.1:8000")
            return 0

        if args.command == "runs":
            run_root = user_dir() / "runs"
            if args.runs_action == "list":
                if not run_root.exists():
                    print("No saved requests.")
                else:
                    for path in sorted(run_root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                        try:
                            meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
                            print(f"{path.name}\t{meta.get('mode_used')}\t{meta.get('verdict')}\t{meta.get('model_used')}")
                        except (OSError, json.JSONDecodeError):
                            continue
            else:
                path = run_root / args.request_id
                meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
                print(json.dumps(meta, ensure_ascii=False, indent=2))
            return 0

    except (AtomicFlowError, ConfigurationError, OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        print(f"atomic: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
