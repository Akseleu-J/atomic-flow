"""Root-qualified read tool, available only with an explicitly supplied host session."""
from pydantic import BaseModel, ConfigDict, Field

from atomic_flow.agents.base import Tool, ToolContract
from atomic_flow.agents.source import SourceConflict


class WorkspaceReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root_id: str = Field(min_length=1, max_length=48)
    path: str = Field(min_length=1, max_length=500)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=6000, ge=1, le=12000)


class WorkspaceReadTool(Tool):
    name = "workspace_read"
    description = "Read bounded redacted source from an explicitly host-approved workspace root ID."
    args_schema = WorkspaceReadArgs
    contract = ToolContract(effects=("read",))

    def run(self, args, ctx):
        ctx.cancel_check()
        session = getattr(ctx, "workspace_session", None)
        if session is None:
            return {"error": "host did not supply a workspace root session"}
        try:
            # Model reads cannot create host mutation capabilities. A host which
            # wants to publish through this session performs its own explicit read.
            result = session.read(args.root_id, args.path, offset=args.offset, limit=args.limit, record_proof=False)
            ctx.cancel_check()
            return result
        except (SourceConflict, ValueError, UnicodeError) as exc:
            return {"error": str(exc), "root_id": args.root_id, "path": args.path}
