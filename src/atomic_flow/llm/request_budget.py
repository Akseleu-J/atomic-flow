"""Offline request admission, including instructions, schemas and reply reserve.

The default estimates one token per serialized UTF-8 byte, plus configured framing.
This deliberately overestimates common text tokenizers; it is NOT a universal upper
bound, a provider tokenizer, or a billing guarantee. Unknown model limits remain
explicitly unknown. Callers may inject a tokenizer matching their own deployment;
even then provider-specific framing must be configured separately.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from atomic_flow.config import BackendConfig, ConversationConfig
from atomic_flow.errors import ContextLimitError

TokenCounter = Callable[[str], int]


def _serialized(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class RequestAdmission:
    model: str
    serialized_request_bytes: int
    estimated_input_tokens: int
    output_reserve_tokens: int
    framing_tokens: int
    context_window_tokens: int | None
    max_input_tokens: int | None
    count_method: str
    provider_token_count_verified: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def estimate_request(
    messages: list[dict[str, Any]],
    backend_config: BackendConfig,
    *,
    output_reserve_tokens: int | None = None,
    tool_schemas: list[dict[str, Any]] | None = None,
    token_counter: TokenCounter | None = None,
) -> RequestAdmission:
    """Measure the rendered request, not just retrieved source snippets.

    Native tool schemas must be supplied here if the transport adds them. Current
    text-protocol tool descriptions are already part of the system messages.
    The caller must send the same output limit as the reserve used for admission.
    """
    if output_reserve_tokens is not None and (not isinstance(output_reserve_tokens, int) or output_reserve_tokens < 0):
        raise ValueError("output_reserve_tokens must be a nonnegative integer")
    reserve = max(backend_config.max_output_tokens or 0, output_reserve_tokens or 0)
    if backend_config.max_output_tokens is None and output_reserve_tokens is None:
        reserve = 4096
    payload: Any = {"messages": messages, "tools": tool_schemas} if tool_schemas else messages
    serialized = _serialized(payload)
    byte_count = len(serialized.encode("utf-8"))
    content_tokens = token_counter(serialized) if token_counter else byte_count
    if not isinstance(content_tokens, int) or isinstance(content_tokens, bool) or content_tokens < 0:
        raise ValueError("token_counter must return a nonnegative integer")
    framing = backend_config.request_framing_tokens + backend_config.message_framing_tokens * len(messages)
    return RequestAdmission(
        model=backend_config.model,
        serialized_request_bytes=byte_count,
        estimated_input_tokens=content_tokens + framing,
        output_reserve_tokens=reserve,
        framing_tokens=framing,
        context_window_tokens=backend_config.context_window_tokens,
        max_input_tokens=backend_config.max_input_tokens,
        count_method="injected_tokenizer_with_configured_framing" if token_counter else "utf8_bytes_estimate_with_configured_framing",
    )


def admit_request(
    messages: list[dict[str, Any]],
    backend_config: BackendConfig,
    conversation: ConversationConfig | None = None,
    *,
    output_reserve_tokens: int | None = None,
    tool_schemas: list[dict[str, Any]] | None = None,
    token_counter: TokenCounter | None = None,
) -> RequestAdmission:
    """Raise before any backend call when byte/input/context admission fails."""
    settings = conversation or ConversationConfig()
    admission = estimate_request(messages, backend_config, output_reserve_tokens=output_reserve_tokens,
                                 tool_schemas=tool_schemas, token_counter=token_counter)
    if admission.serialized_request_bytes > settings.max_request_bytes:
        raise ContextLimitError("request exceeds conversation.max_request_bytes; instructions/current evidence retained, call not sent")
    if admission.max_input_tokens is not None and admission.estimated_input_tokens > admission.max_input_tokens:
        raise ContextLimitError("request estimate exceeds backend.max_input_tokens; call not sent")
    if admission.context_window_tokens is not None and (
        admission.estimated_input_tokens + admission.output_reserve_tokens > admission.context_window_tokens
    ):
        raise ContextLimitError("request estimate plus reply reserve exceeds backend.context_window_tokens; call not sent")
    return admission
