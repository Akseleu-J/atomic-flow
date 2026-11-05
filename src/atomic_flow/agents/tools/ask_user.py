"""Explicit user clarification, independent from tool approval and --yes."""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from atomic_flow.activation.redact import redact
from atomic_flow.agents.base import Tool, ToolContract
from atomic_flow.agents.context import ToolContext


class AskUserArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4096)
    options: list[str] = Field(default_factory=list, max_length=8)


class AskUserTool(Tool):
    name = "ask_user"
    description = "Ask the human for missing requirements. Approval flags never supply an answer."
    args_schema = AskUserArgs
    contract = ToolContract(effects=("interact",), interaction=True, idempotent=False)

    def run(self, args: AskUserArgs, ctx: ToolContext) -> dict[str, Any]:
        ctx.cancel_check()
        if not args.question.strip() or any(not option.strip() or len(option) > 256 for option in args.options):
            return {"error": "question/options must be nonempty and options at most 256 characters", "outcome": "error"}
        question = {"question_id": uuid.uuid4().hex, "question": redact(args.question),
                    "options": [redact(option) for option in args.options]}
        journal = getattr(ctx, "journal_event", None)
        if journal is not None:
            journal("question", question)
        callback = getattr(ctx, "question_callback", None)
        if callback is None:
            return {**question, "error": "no question channel is available", "status": "pending", "outcome": "denied"}
        answer = callback(dict(question))
        ctx.cancel_check()
        if answer is None:
            return {**question, "error": "user has not supplied an answer", "status": "pending", "outcome": "denied"}
        if not isinstance(answer, str) or not answer.strip() or len(answer.encode("utf-8")) > 16384:
            return {**question, "error": "answer must be nonempty text within 16384 UTF-8 bytes", "outcome": "error"}
        result = {**question, "answer": redact(answer), "status": "answered", "outcome": "success"}
        if journal is not None:
            journal("answer", result)
        ctx.cancel_check()
        return result
