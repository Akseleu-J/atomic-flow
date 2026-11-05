"""Deterministic history admission for atomic-flow's text/JSON tool protocol.

Only old tool result bodies are replaceable. Instructions, tool arguments, latest
results and all error/edit proof remain explicit. This is not model summarization.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

from atomic_flow.agents.result import ToolResult
from atomic_flow.config import BackendConfig, ConversationConfig
from atomic_flow.errors import ContextLimitError, RepeatedToolCall
from atomic_flow.llm.request_budget import RequestAdmission, TokenCounter, admit_request


def wire_bytes(messages: list[dict[str, str]]) -> int:
    return len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class RequestHistory:
    def __init__(self, settings: ConversationConfig, backend_config: BackendConfig | None = None, *,
                 output_reserve_tokens: int | None = None, tool_schemas: list[dict] | None = None,
                 token_counter: TokenCounter | None = None, request_renderer=None):
        self.settings = settings
        self.backend_config = backend_config or BackendConfig()
        self.output_reserve_tokens = output_reserve_tokens
        self.tool_schemas = tool_schemas
        self.token_counter = token_counter
        self.request_renderer = request_renderer or (lambda messages: [dict(m) for m in messages])
        self.last_admission: RequestAdmission | None = None
        self.results: list[tuple[int, str]] = []
        self.compacted: set[int] = set()
        self.calls: Counter = Counter()
        self.peak_bytes = 0
        self.last_breakdown: dict = {}
        self.pressure_ratio = 0.0

    def pressure(self, admitted: RequestAdmission) -> float:
        ratios = [admitted.serialized_request_bytes / self.settings.max_request_bytes]
        if admitted.max_input_tokens:
            ratios.append(admitted.estimated_input_tokens / admitted.max_input_tokens)
        if admitted.context_window_tokens:
            ratios.append((admitted.estimated_input_tokens + admitted.output_reserve_tokens) / admitted.context_window_tokens)
        return max(ratios)

    @staticmethod
    def breakdown(messages: list[dict[str, str]], admitted: RequestAdmission) -> dict:
        parts = {"system_and_tools": 0, "source": 0, "tool_results": 0, "query_state_and_history": 0}
        content_bytes = 0
        for message in messages:
            text = message["content"]
            size = len(text.encode("utf-8"))
            content_bytes += size
            if message["role"] == "system":
                parts["system_and_tools"] += size
                continue
            remaining = size
            for match in re.finditer(r"<(project_context|tool_result)>[\s\S]*?</\1>", text):
                amount = len(match.group().encode("utf-8"))
                parts["source" if match[1] == "project_context" else "tool_results"] += amount
                remaining -= amount
            parts["query_state_and_history"] += remaining
        parts["json_envelope_and_escaping"] = admitted.serialized_request_bytes - content_bytes
        return {"serialized_bytes_by_component": parts, "output_reserve_tokens": admitted.output_reserve_tokens,
                "accounting": "Exact serialized byte partition, not provider token counts. System includes text tool schemas."}

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
        def admission():
            return admit_request(self.request_renderer(messages), self.backend_config, self.settings,
                                 output_reserve_tokens=self.output_reserve_tokens,
                                 tool_schemas=self.tool_schemas, token_counter=self.token_counter)

        try:
            admitted = admission()
        except ContextLimitError:
            admitted = None
        # Compact oldest bodies first, keeping the newest configured results intact.
        candidates = self.results[:-self.settings.recent_tool_results]
        for index, compact in candidates:
            if admitted is not None and self.pressure(admitted) < self.settings.compact_at_ratio:
                break
            if index in self.compacted:
                continue
            if len(compact.encode("utf-8")) >= len(messages[index]["content"].encode("utf-8")):
                continue
            messages[index] = {"role": "user", "content": compact}
            self.compacted.add(index)
            try:
                admitted = admission()
            except ContextLimitError:
                admitted = None
        if admitted is None:
            admitted = admission()  # Preserve the precise failed limit and fail closed.
        self.last_admission = admitted
        self.pressure_ratio = self.pressure(admitted)
        self.last_breakdown = self.breakdown(self.request_renderer(messages), admitted)
        self.peak_bytes = max(self.peak_bytes, admitted.serialized_request_bytes)
        # Backends see a stable snapshot; future compaction cannot mutate prior audit captures.
        return self.request_renderer(messages)

    def stats(self) -> dict:
        return {"compacted_tool_results": len(self.compacted), "peak_request_bytes": self.peak_bytes,
                "max_request_bytes": self.settings.max_request_bytes,
                "pressure_ratio": self.pressure_ratio, "compact_at_ratio": self.settings.compact_at_ratio,
                "near_capacity": self.pressure_ratio >= self.settings.compact_at_ratio,
                "context_breakdown": self.last_breakdown,
                "request_admission": self.last_admission.as_dict() if self.last_admission else None}
