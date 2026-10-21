"""Deterministic history admission for atomic-flow's text/JSON tool protocol.

Only old tool result bodies are replaceable. Instructions, tool arguments, latest
results and all error/edit proof remain explicit. This is not model summarization.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from atomic_flow.agents.result import ToolResult
from atomic_flow.config import ConversationConfig
from atomic_flow.errors import ContextLimitError, RepeatedToolCall


def wire_bytes(messages: list[dict[str, str]]) -> int:
    return len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class RequestHistory:
    def __init__(self, settings: ConversationConfig):
        self.settings = settings
        self.results: list[tuple[int, str]] = []
        self.compacted: set[int] = set()
        self.calls: Counter = Counter()
        self.peak_bytes = 0

    def check_tool(self, parsed: dict, revision: int):
        key = (revision, hashlib.sha256(json.dumps(parsed, sort_keys=True, ensure_ascii=False,
                                                  separators=(",", ":")).encode("utf-8")).hexdigest())
        self.calls[key] += 1
        if self.calls[key] > self.settings.max_same_tool_call:
            raise RepeatedToolCall(f"repeated tool request: {parsed['tool']}; no source revision changed")

    def append_result(self, messages: list[dict[str, str]], result: dict):
        full = {"role": "user", "content": f"<tool_result>{json.dumps(result, ensure_ascii=False)}</tool_result>"}
        proof = ToolResult.from_payload(result, outcome=result.get("outcome")).summary()
        # Preserve pagination and provenance even when a source body leaves the prompt.
        for key in ("offset", "next_offset", "truncated", "lines", "zoom"):
            if key in result:
                proof[key] = result[key]
        proof["history_body_omitted"] = True
        proof["history_notice"] = "Old tool body omitted to fit request; reread if needed. This is not full source evidence."
        compact = f"<tool_result>{json.dumps(proof, ensure_ascii=False)}</tool_result>"
        self.results.append((len(messages), compact))
        messages.append(full)

    def prepare(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        size = wire_bytes(messages)
        # Compact oldest bodies first, keeping the newest configured results intact.
        candidates = self.results[:-self.settings.recent_tool_results]
        for index, compact in candidates:
            if size <= self.settings.max_request_bytes:
                break
            if index in self.compacted:
                continue
            if len(compact.encode("utf-8")) >= len(messages[index]["content"].encode("utf-8")):
                continue
            messages[index] = {"role": "user", "content": compact}
            self.compacted.add(index)
            size = wire_bytes(messages)
        if size > self.settings.max_request_bytes:
            raise ContextLimitError("request exceeds conversation.max_request_bytes; instructions/current evidence retained, call not sent")
        self.peak_bytes = max(self.peak_bytes, size)
        # Backends see a stable snapshot; future compaction cannot mutate prior audit captures.
        return [dict(message) for message in messages]

    def stats(self) -> dict:
        return {"compacted_tool_results": len(self.compacted), "peak_request_bytes": self.peak_bytes,
                "max_request_bytes": self.settings.max_request_bytes}
