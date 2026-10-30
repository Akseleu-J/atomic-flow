"""Read-only language queries through an already approved host-owned client."""
from pydantic import BaseModel, ConfigDict, Field

from atomic_flow.agents.base import Tool, ToolContract
from atomic_flow.agents.source import SourceConflict


class LanguagePathArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)


class LanguagePositionArgs(LanguagePathArgs):
    line: int = Field(ge=0)
    character: int = Field(ge=0, description="UTF-16 code units, zero based")


def _client(ctx):
    client = ctx.lsp_client
    if client is None:
        raise SourceConflict("host did not supply an explicitly approved LSP client")
    if client.project != ctx.project_root.resolve():
        raise SourceConflict("LSP client belongs to a different project")
    return client


class LSPDefinitionTool(Tool):
    name = "lsp_definition"
    description = "Query definitions with current file hashes using an already host-approved language server. Does not read bodies or authorize edits."
    args_schema = LanguagePositionArgs
    contract = ToolContract(effects=("read",), timeout_seconds=30)

    def run(self, args, ctx):
        ctx.cancel_check()
        client = _client(ctx)
        with client.operation(cancel_check=ctx.cancel_check):
            results = client.definition(args.path, args.line, args.character)
        ctx.cancel_check()
        return {"path": args.path, "results": results, "data_only": True, "read_complete": False}


class LSPReferencesTool(LSPDefinitionTool):
    name = "lsp_references"
    description = "Query references with current file hashes through an already host-approved language server. Locations are advisory server data."

    def run(self, args, ctx):
        ctx.cancel_check()
        client = _client(ctx)
        with client.operation(cancel_check=ctx.cancel_check):
            results = client.references(args.path, args.line, args.character)
        ctx.cancel_check()
        return {"path": args.path, "results": results, "data_only": True, "read_complete": False}


class LSPDiagnosticsTool(LSPDefinitionTool):
    name = "lsp_diagnostics"
    description = "Read latest version/hash-bound language-server diagnostics. Unavailable diagnostics are explicit and do not prove correctness."
    args_schema = LanguagePathArgs

    def run(self, args, ctx):
        ctx.cancel_check()
        client = _client(ctx)
        with client.operation(cancel_check=ctx.cancel_check):
            result = client.diagnostics(args.path)
        ctx.cancel_check()
        return {**result, "data_only": True, "read_complete": False}
