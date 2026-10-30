"""Host-owned native call/result IDs around the existing internal tool loop."""
from __future__ import annotations

import json


def native_schemas(schemas: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": tool["name"], "description": tool["description"],
                                             "parameters": tool["input_schema"]}} for tool in schemas]


def native_messages(messages: list[dict]) -> list[dict]:
    """Translate host-marked assistant calls and results to Chat Completions wire format.

    Plain model text cannot create an API tool-call ID. Calls deferred for scoped
    instructions get an explicit non-execution result before the following message.
    """
    wire = []
    pending = None
    seen = set()
    for message in messages:
        calls = message.get("_native_tool_calls")
        if calls:
            if pending or len(calls) != 1 or calls[0]["id"] in seen:
                raise ValueError("Invalid or repeated native call pairing")
            pending = calls[0]["id"]
            seen.add(pending)
            wire.append({"role": "assistant", "content": "", "tool_calls": calls})
            continue
        if pending:
            content = message.get("content", "")
            if message["role"] == "user" and content.startswith("<tool_result>") and content.endswith("</tool_result>"):
                wire.append({"role": "tool", "tool_call_id": pending, "content": content})
                pending = None
                continue
            wire.append({"role": "tool", "tool_call_id": pending,
                         "content": json.dumps({"outcome": "denied", "error": "Call was not executed; review updated guidance."})})
            pending = None
        wire.append({key: value for key, value in message.items() if not key.startswith("_")})
    if pending:
        raise ValueError("Native tool call has no host result")
    return wire
