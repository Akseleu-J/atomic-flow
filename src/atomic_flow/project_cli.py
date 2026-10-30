"""Explicit notebook/root/watch/language-server command surfaces; lazy host APIs."""
from __future__ import annotations

import json
import time
from pathlib import Path

from atomic_flow.config import user_dir


def add_project_parsers(cmd):
    docs = cmd.add_parser("docs", help="Read/search explicit public HTTPS documentation domains")
    ds = docs.add_subparsers(dest="docs_action", required=True)
    for action in ("fetch", "search"):
        sub = ds.add_parser(action)
        sub.add_argument("value", help="URL for fetch, query for search")
        sub.add_argument("--host", action="append", required=True, help="Explicit exact allowed host")
        if action == "search":
            sub.add_argument("--url", action="append", required=True, help="Explicit documentation page to search")
    components = cmd.add_parser("components", help="Inspect deferred builtin components without starting them")
    components.add_argument("name", nargs="?")
    plugin = cmd.add_parser("plugin", help="Validate/inspect a declarative plugin manifest; does not load Python")
    plugin.add_argument("manifest", type=Path)
    workflow = cmd.add_parser("workflow", help="Review a declarative workflow; execution is an approved host SDK operation")
    workflow.add_argument("manifest", type=Path)
    mcp = cmd.add_parser("mcp", help="MCP source server or explicit trusted client metadata")
    ms = mcp.add_subparsers(dest="mcp_action", required=True)
    for action in ("serve", "tools"):
        sub = ms.add_parser(action)
        sub.add_argument("--project", default=".")
        if action == "tools":
            sub.add_argument("--command-json", required=True)
            sub.add_argument("--yes", action="store_true")
    ide = cmd.add_parser("ide", help="Authenticated read-only loopback IDE source bridge")
    ide.add_argument("--project", default=".")
    ide.add_argument("--origin", action="append", required=True)
    ide.add_argument("--port", type=int, default=8767)
    ide.add_argument("--token-env", default="ATOMIC_FLOW_IDE_TOKEN")
    notebook = cmd.add_parser("notebook", help="Source-only notebook cells; never executes a kernel")
    ns = notebook.add_subparsers(dest="notebook_action", required=True)
    for action in ("read", "preview", "apply", "normalize-ids"):
        sub = ns.add_parser(action)
        sub.add_argument("path")
        sub.add_argument("--project", default=".")
        sub.add_argument("--expected-hash")
        if action == "read":
            sub.add_argument("--offset", type=int, default=0)
            sub.add_argument("--limit", type=int, default=6000)
        if action in {"preview", "apply"}:
            sub.add_argument("--operation-json", required=True)
        if action in {"apply", "normalize-ids"}:
            sub.add_argument("--preview-token")
            sub.add_argument("--yes", action="store_true")
    workspace = cmd.add_parser("workspace", help="Explicit root grants and root-qualified source reads")
    ws = workspace.add_subparsers(dest="workspace_action", required=True)
    for action in ("list", "grant", "revoke", "read", "list-files"):
        sub = ws.add_parser(action)
        sub.add_argument("--workspace", default="default")
        if action != "list":
            sub.add_argument("root_id")
        if action == "grant":
            sub.add_argument("path", type=Path)
            sub.add_argument("--write-path", action="append", default=[])
        if action in {"grant", "revoke"}:
            sub.add_argument("--yes", action="store_true")
        if action == "read":
            sub.add_argument("path")
            sub.add_argument("--offset", type=int, default=0)
            sub.add_argument("--limit", type=int, default=6000)
        if action == "list-files":
            sub.add_argument("--limit", type=int, default=200)
    watch = cmd.add_parser("watch", help="Bounded source polling events; Ctrl-C stops")
    watch.add_argument("--project", default=".")
    watch.add_argument("--interval", type=float, default=0.5)
    watch.add_argument("--debounce", type=float, default=0.2)
    watch.add_argument("--iterations", type=int, default=0, help="0 means until Ctrl-C")
    lsp = cmd.add_parser("lsp", help="Explicit trusted server; no project executable discovery")
    ls = lsp.add_subparsers(dest="lsp_action", required=True)
    for action in ("definition", "references", "diagnostics", "preview-rename", "recover"):
        sub = ls.add_parser(action)
        sub.add_argument("path", help="Source path, or fileedit ID for recover")
        sub.add_argument("--project", default=".")
        sub.add_argument("--yes", action="store_true", help="Explicitly authorize trusted server or owned recovery")
        if action != "recover":
            sub.add_argument("--command-json", required=True, help="JSON argv, absolute trusted executable")
        if action in {"definition", "references", "preview-rename"}:
            sub.add_argument("--line", type=int, default=0)
            sub.add_argument("--character", type=int, default=0, help="Zero-based UTF-16 units")
        if action == "preview-rename":
            sub.add_argument("--new-name", required=True)
            sub.add_argument("--read-path", action="append", default=[])


def _notebook(args):
    from atomic_flow.notebooks import NotebookDocument
    document = NotebookDocument.load(Path(args.project), args.path, expected_hash=args.expected_hash)
    if args.notebook_action == "read":
        return document.read(offset=args.offset, limit=args.limit)
    normalizing = args.notebook_action == "normalize-ids"
    if not normalizing and len(args.operation_json.encode()) > 1_100_000:
        raise ValueError("notebook operation exceeds byte limit")
    preview = document.preview_normalize_ids() if normalizing else document.preview(json.loads(args.operation_json))
    applying = args.notebook_action == "apply" or (normalizing and args.preview_token is not None)
    if not applying:
        return preview.payload()
    if not args.yes or not args.expected_hash or not args.preview_token:
        raise ValueError("notebook apply requires --yes, --expected-hash and reviewed --preview-token")
    return document.apply(preview, storage=user_dir() / "edits", approved=True, preview_token=args.preview_token,
                          allow_normalization=normalizing)


def _workspace(args):
    from atomic_flow.workspace import WorkspaceRegistry
    registry = WorkspaceRegistry(args.workspace)
    action = args.workspace_action
    if action == "list":
        return registry.list_roots()
    if action == "grant":
        return registry.grant(args.root_id, args.path, write_paths=args.write_path, approved=args.yes)
    if action == "revoke":
        return registry.revoke(args.root_id, approved=args.yes)
    if action == "read":
        return registry.session().read(args.root_id, args.path, offset=args.offset, limit=args.limit)
    return registry.session().list_files(args.root_id, limit=args.limit)


def _watch(args):
    from atomic_flow.watching import SourceWatcher
    if not 0 <= args.iterations <= 100000:
        raise ValueError("invalid watch iteration count")
    watcher = SourceWatcher(Path(args.project), interval=args.interval, debounce=args.debounce)
    print(json.dumps({"version": 1, "event": "watch_started", "project": str(watcher.project)}), flush=True)
    try:
        iteration = 0
        while args.iterations == 0 or iteration < args.iterations:
            event = watcher.poll()
            if event is not None and event.ready:
                print(json.dumps({"event": "source_changed", **event.observation()}), flush=True)
                watcher.acknowledge(event)
            iteration += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


def _lsp(args):
    from atomic_flow.lsp import LSPClient, LSPError, recover_lsp_edit
    if not args.yes:
        raise ValueError("LSP requires --yes for a trusted host executable or owned recovery")
    project = Path(args.project).expanduser().resolve()
    try:
        if args.lsp_action == "recover":
            return recover_lsp_edit(project, args.path, approved=True)
        if len(args.command_json.encode()) > 32768:
            raise ValueError("LSP command exceeds byte limit")
        with LSPClient(project, json.loads(args.command_json), approved=True) as client:
            client.read_document(args.path)
            if args.lsp_action == "diagnostics":
                return client.diagnostics(args.path)
            if args.lsp_action == "preview-rename":
                for path in args.read_path:
                    client.read_document(path)
                return client.preview_rename(args.path, args.line, args.character, args.new_name).summary()
            return getattr(client, args.lsp_action)(args.path, args.line, args.character)
    except LSPError as exc:
        raise ValueError(str(exc)) from None


def dispatch(args):
    if args.command in {"docs", "components", "plugin", "workflow", "mcp", "ide"}:
        return _integration(args)
    if args.command == "watch":
        _watch(args)
    else:
        data = {"notebook": _notebook, "workspace": _workspace, "lsp": _lsp}[args.command](args)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _integration(args):
    if args.command == "docs":
        from atomic_flow.documentation import DocumentationClient
        client = DocumentationClient(args.host, approved=True)
        data = client.fetch(args.value) if args.docs_action == "fetch" else client.search(args.value, args.url)
    elif args.command == "components":
        from atomic_flow.extensions import OptionalComponentUnavailable
        from atomic_flow.sdk import components
        catalog = components()
        data = catalog.diagnostics()
        if args.name:
            try:
                factory = catalog.load(args.name)
            except OptionalComponentUnavailable as exc:
                raise ValueError(str(exc)) from None
            data = {"name": args.name, "loaded_symbol": factory.__name__, "diagnostics": catalog.diagnostics()}
    elif args.command in {"plugin", "workflow"}:
        with args.manifest.open("rb") as handle:
            raw = handle.read(131073)
        if len(raw) > 131072:
            raise ValueError("manifest exceeds byte limit")
        if args.command == "plugin":
            from atomic_flow.extensions import PluginManifest
            parsed = PluginManifest.model_validate(json.loads(raw))
        else:
            from atomic_flow.workflows import Workflow
            parsed = Workflow.model_validate(json.loads(raw))
        data = {"manifest": parsed.model_dump(mode="json"), "digest": parsed.digest(), "execution_performed": False}
    elif args.command == "mcp":
        from atomic_flow.integrations.mcp import MCPClient, MCPError, serve_mcp
        if args.mcp_action == "serve":
            serve_mcp(Path(args.project))
            return 0
        if not args.yes:
            raise ValueError("MCP client command requires explicit --yes for trusted host code")
        if len(args.command_json.encode()) > 32768:
            raise ValueError("MCP command exceeds byte limit")
        try:
            with MCPClient(Path(args.project), json.loads(args.command_json), approved=True) as client:
                data = client.list_tools()
        except MCPError as exc:
            raise ValueError(str(exc)) from None
    else:
        import os

        from atomic_flow.integrations.ide import create_ide_bridge
        server = create_ide_bridge(Path(args.project), token=os.environ.get(args.token_env, ""), allowed_origins=args.origin, port=args.port)
        print(json.dumps({"host": "127.0.0.1", "port": server.server_port, "project_id": server.project_id,
                          "nonce": server.nonce, "access": "read_only"}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0
