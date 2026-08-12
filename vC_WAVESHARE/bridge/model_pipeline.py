"""Bounded Responses tool orchestration with injected provider and tool adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from bridge_contracts import GeneratedModelText, ModelActivity
from bridge_policy import BridgePolicy
from model_protocol import TerminalReplyState, ToolRequestState, classify_response
from sphere_tools import (
    explicit_web_search_requested,
    forced_memory_mutation_tool_for_turn,
    memory_confirmation_decision_for_turn,
    memory_mutation_tools_for_turn,
    parse_function_arguments,
    sensitive_memory_candidate_for_turn,
    source_read_requested_for_turn,
    web_search_requested_for_turn,
)
from tool_activity import (
    collect_response_sources,
    model_tool_context,
    public_tool_activity,
    record_web_search_activity,
)


INITIAL_FAILURE_TEXT = (
    "RF\nI hit an internal error before I could finish that request. Please try again."
)
FOLLOWUP_FAILURE_TEXT = (
    "RF\nI completed the tool attempts shown above, but hit an internal error "
    "before I could finish replying. Please try again."
)
ROUND_LIMIT_TEXT = (
    "RF\nI completed the tool attempts shown above, but reached my per-message "
    "tool limit before I could finish replying. Send a short follow-up and I’ll continue."
)


@dataclass(frozen=True, slots=True)
class ModelRequestContext:
    instructions: str
    input_messages: tuple[dict[str, str], ...]
    model: str
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class ToolTurnPlan:
    tools: tuple[dict[str, Any], ...]
    tool_choice: Any | None
    web_search_enabled: bool
    reset_tool_choice_after_first_response: bool


def build_model_request_context(
    *,
    system_instructions: str,
    history_messages: Iterable[dict[str, Any]],
    user_transcript: str,
    memories: Iterable[str],
    model: str,
    max_output_tokens: int,
    tool_instructions: str = "",
) -> ModelRequestContext:
    """Build the provider-neutral context without calling a model or store."""
    instructions = system_instructions
    memory_values = [str(memory) for memory in memories if str(memory).strip()]
    if memory_values:
        memory_context = "\n".join(f"- {memory}" for memory in memory_values)
        instructions += (
            "\n\nRelevant durable user memory follows. Treat it as potentially stale, "
            "use it only when relevant, and prefer the user's current statement if it conflicts:\n"
            f"{memory_context}"
        )
    instructions += tool_instructions
    input_messages = tuple(
        {
            "role": str(message.get("role", "user")),
            "content": str(message.get("content", "")),
        }
        for message in history_messages
    ) + ({"role": "user", "content": user_transcript},)
    return ModelRequestContext(
        instructions=instructions,
        input_messages=input_messages,
        model=model,
        max_output_tokens=max_output_tokens,
    )


def build_tool_turn_plan(
    *,
    policy: BridgePolicy,
    base_tools: Iterable[dict[str, Any]],
    user_transcript: str,
    memory_available: bool,
    source_available: bool,
    web_search_configured: bool,
    pending_memory_confirmation: bool,
) -> ToolTurnPlan:
    tools = list(base_tools)
    memory_tools = memory_mutation_tools_for_turn(user_transcript)
    forced_memory_tool = forced_memory_mutation_tool_for_turn(user_transcript)
    if explicit_web_search_requested(user_transcript):
        forced_memory_tool = None
    confirmation_decision = memory_confirmation_decision_for_turn(user_transcript)
    force_confirmation = bool(pending_memory_confirmation and confirmation_decision is not None)
    force_sensitive_stage = bool(
        not force_confirmation
        and not memory_tools
        and sensitive_memory_candidate_for_turn(user_transcript)
    )
    force_source_read = bool(
        source_available
        and not force_confirmation
        and not force_sensitive_stage
        and not memory_tools
        and source_read_requested_for_turn(user_transcript)
    )
    eligible_names = policy.eligible_tool_names(
        (
            str(tool.get("name"))
            for tool in tools
            if tool.get("type") == "function" and tool.get("name")
        ),
        memory_available=memory_available,
        source_available=source_available,
        memory_mutation_tool_names=memory_tools,
        force_memory_confirmation=force_confirmation,
        force_sensitive_stage=force_sensitive_stage,
    )
    eligible_tools = [tool for tool in tools if str(tool.get("name")) in eligible_names]
    web_search = policy.web_search_eligible(
        configured=web_search_configured,
        requested_for_turn=web_search_requested_for_turn(user_transcript),
    )
    if web_search:
        eligible_tools.append({"type": "web_search"})

    tool_choice: Any | None = None
    if force_confirmation:
        tool_choice = {"type": "function", "name": "memory_confirm"}
    elif force_sensitive_stage:
        tool_choice = {"type": "function", "name": "memory_remember"}
    elif forced_memory_tool:
        tool_choice = {"type": "function", "name": forced_memory_tool}
    elif force_source_read:
        tool_choice = {"type": "function", "name": "source_read"}

    return ToolTurnPlan(
        tools=tuple(eligible_tools),
        tool_choice=tool_choice,
        web_search_enabled=web_search,
        reset_tool_choice_after_first_response=tool_choice is not None,
    )


class ModelToolPipeline:
    """Run one bounded tool turn without owning provider, policy, or tool mutation."""

    def __init__(
        self,
        *,
        response_create: Callable[..., Any],
        tool_execute: Callable[..., dict[str, Any]],
        max_tool_rounds: int,
        emit_exception: Callable[[str, BaseException], None],
        emit_round_limit: Callable[[int], None],
    ) -> None:
        self.response_create = response_create
        self.tool_execute = tool_execute
        self.max_tool_rounds = max(1, min(int(max_tool_rounds), 8))
        self.emit_exception = emit_exception
        self.emit_round_limit = emit_round_limit

    def run(
        self,
        context: ModelRequestContext,
        plan: ToolTurnPlan,
    ) -> GeneratedModelText:
        activity = ModelActivity()
        request_options: dict[str, Any] = {
            "model": context.model,
            "instructions": context.instructions,
            "input": list(context.input_messages),
            "tools": list(plan.tools),
            "parallel_tool_calls": False,
            "max_output_tokens": context.max_output_tokens,
        }
        if plan.tool_choice is not None:
            request_options["tool_choice"] = plan.tool_choice
        if plan.web_search_enabled:
            request_options["include"] = ["web_search_call.action.sources"]

        try:
            response = self.response_create(**request_options)
        except Exception as exc:
            self.emit_exception("bridge.initial_agent_response_failed", exc)
            return GeneratedModelText(raw_text=INITIAL_FAILURE_TEXT, activity=activity)

        self._record_response_activity(response, activity)
        for _round_index in range(self.max_tool_rounds):
            state = classify_response(response)
            if isinstance(state, TerminalReplyState):
                return GeneratedModelText(raw_text=state.raw_text, activity=activity)

            outputs = self._execute_requests(state, activity)
            followup_options = {
                **request_options,
                "previous_response_id": state.response_id,
                "input": outputs,
            }
            if plan.reset_tool_choice_after_first_response:
                followup_options["tool_choice"] = "auto"
            try:
                response = self.response_create(**followup_options)
            except Exception as exc:
                self.emit_exception("bridge.agent_followup_failed", exc)
                return GeneratedModelText(
                    raw_text=FOLLOWUP_FAILURE_TEXT,
                    activity=activity,
                )
            self._record_response_activity(response, activity)

        self.emit_round_limit(self.max_tool_rounds)
        return GeneratedModelText(raw_text=ROUND_LIMIT_TEXT, activity=activity)

    def _execute_requests(
        self,
        state: ToolRequestState,
        activity: ModelActivity,
    ) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        for request in state.requests:
            arguments: dict[str, Any] = {}
            try:
                arguments = parse_function_arguments(request.raw_arguments)
                result = self.tool_execute(
                    request.name,
                    arguments,
                    call_id=request.call_id,
                )
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            activity.tool_results.append(
                public_tool_activity(request.name, result, arguments)
            )
            activity.model_tool_context.append(
                model_tool_context(request.name, result, arguments)
            )
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": request.call_id,
                    "output": json.dumps(result, ensure_ascii=False, default=str),
                }
            )
        return outputs

    @staticmethod
    def _record_response_activity(response: Any, activity: ModelActivity) -> None:
        collect_response_sources(response, activity.sources)
        record_web_search_activity(response, activity)
