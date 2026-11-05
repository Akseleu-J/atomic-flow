"""Bounded parallel read calls with per-child dispatch and revision validation."""
from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, ConfigDict, Field

from atomic_flow.agents.base import Tool, ToolContract
from atomic_flow.agents.source import confined_path, digest

SAFE_READS = {"read_file", "read_node", "find_files", "find_callers", "search_tree", "read_result"}


class ReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    args: dict = Field(default_factory=dict)


class ReadManyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requests: list[ReadRequest] = Field(min_length=1, max_length=8)


class ReadManyTool(Tool):
    name = "read_many"
    description = ('Read up to 8 independent items with at most 4 workers: requests=[{"tool":"read_file","args":{"path":"a.py"}}]. '
                   "Only role-allowed safe reads with automatic permission; no writes, execution, approval prompts or recursion.")
    args_schema = ReadManyArgs
    contract = ToolContract(effects=("read",), timeout_seconds=60)

    def run(self, args, ctx):
        from atomic_flow.agents.loop import execute_tool
        from atomic_flow.agents.policy import PolicyEngine
        from atomic_flow.agents.tools import default_registry

        allowed = set(ctx.active_allowed_tools)
        registry = default_registry()
        revision = ctx.revision
        hashes = dict(ctx.file_hashes)
        # Preflight the entire batch before starting a child. Never prompt on worker threads.
        for request in args.requests:
            if request.tool not in SAFE_READS or request.tool not in allowed:
                return {"error": "batch contains a read not allowed for this role", "tool": request.tool}
            tool = registry.get(request.tool)
            parsed = tool.args_schema.model_validate(request.args)
            policy_args = parsed.model_dump()
            if "node_id" in policy_args:
                node = ctx.nodes_by_id.get(ctx.alias_to_id.get(policy_args["node_id"], policy_args["node_id"]))
                if node:
                    policy_args["path"] = node.path
            if (ctx.policy or PolicyEngine()).decide(request.tool, policy_args, project_trusted=ctx.trusted).action != "auto":
                return {"error": "parallel reads require automatic permission for every child", "tool": request.tool}

        def run(request):
            child = copy.copy(ctx)
            child.reads = copy.deepcopy(ctx.reads)
            child.evidence = dict(ctx.evidence)
            child.file_hashes = dict(hashes)
            child.nodes_by_id = dict(ctx.nodes_by_id)
            child.approval_callback = None
            result, record = execute_tool(request.model_dump(), registry, child,
                                          allowed_tools=ctx.active_allowed_tools, role=ctx.active_role)
            return result, record, child

        ctx.cancel_check()
        with ThreadPoolExecutor(max_workers=min(4, len(args.requests)), thread_name_prefix="atomic-read") as pool:
            children = list(pool.map(run, args.requests))
        ctx.cancel_check()
        if ctx.revision != revision or ctx.file_hashes != hashes:
            return {"error": "index revision changed during parallel read; results discarded"}
        touched = {read["path"] for _, _, child in children for read in child.reads}
        touched.update(node.path for request in args.requests if request.tool in {"read_node", "find_callers"}
                       if (node := ctx.nodes_by_id.get(ctx.alias_to_id.get(request.args.get("node_id"), request.args.get("node_id")))))
        for path in touched:
            if digest(confined_path(ctx.project_root, path).read_bytes()) != hashes[path]:
                return {"error": "source changed during parallel read; results discarded", "path": path}
        payload = {"results": [r for r, _, _ in children], "calls": [r for _, r, _ in children],
                   "index_revision": revision, "workers": min(4, len(args.requests)),
                   **({"error": "one or more parallel reads failed"} if any(r.get("outcome") != "success" for r, _, _ in children) else {})}
        if len(json.dumps(payload, ensure_ascii=False).encode()) > self.contract.max_output_bytes:
            return {"error": "parallel read output exceeds its limit; use smaller pages/batches; coverage not recorded"}
        for result, _, child in children:
            if result.get("outcome") == "success":
                for read in child.reads:
                    for start, stop in read["ranges"]:
                        ctx.record_read(read["path"], read["content_hash"], read["view"], start, stop, read["total"],
                                        node_id=read["node_id"], source_complete=read["source_complete"])
                for node_id, text in child.evidence.items():
                    if text not in ctx.evidence.get(node_id, ""):
                        ctx.evidence[node_id] = (ctx.evidence.get(node_id, "") + "\n" + text).strip()
        return payload
