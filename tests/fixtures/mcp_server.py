"""Trusted local protocol fixture; no model or external service."""
import json
import os
import sys
import time
from pathlib import Path

from atomic_flow.integrations.mcp import PROTOCOL_VERSION, serve_mcp

if len(sys.argv) > 2 and sys.argv[2] in {"hang", "hang-init"}:
    for line in sys.stdin:
        value = json.loads(line)
        if sys.argv[2] == "hang-init":
            time.sleep(30)
        if value.get("method") == "initialize":
            print(json.dumps({"jsonrpc": "2.0", "id": value["id"], "result": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}}}), flush=True)
        elif value.get("method") == "tools/list":
            time.sleep(30)
elif len(sys.argv) > 2 and sys.argv[2] == "oversize":
    print("x" * (1024 * 1024 + 1), flush=True)
else:
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps({key: os.environ.get(key) for key in ("MCP_TEST_SECRET", "ATOMIC_FLOW_HOME", "GITHUB_TOKEN")}), encoding="utf-8")
    serve_mcp(Path(sys.argv[1]))
