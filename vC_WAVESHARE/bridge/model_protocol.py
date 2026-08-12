"""Provider-independent model output parsing and explicit pipeline states."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from bridge_contracts import LED_COMMAND_CODES, ParsedModelReply


class ModelOutputState(str, Enum):
    FALLBACK = "fallback"
    PARSED_REPLY = "parsed_reply"
    TOOL_REQUEST = "tool_request"
    TERMINAL_REPLY = "terminal_reply"


@dataclass(frozen=True, slots=True)
class FallbackState:
    raw_text: str
    parsed: ParsedModelReply
    reason: str
    state: ModelOutputState = ModelOutputState.FALLBACK


@dataclass(frozen=True, slots=True)
class ParsedReplyState:
    raw_text: str
    parsed: ParsedModelReply
    strategy: str
    state: ModelOutputState = ModelOutputState.PARSED_REPLY


@dataclass(frozen=True, slots=True)
class ToolRequest:
    name: str
    call_id: str
    raw_arguments: Any


@dataclass(frozen=True, slots=True)
class ToolRequestState:
    response_id: str
    requests: tuple[ToolRequest, ...]
    state: ModelOutputState = ModelOutputState.TOOL_REQUEST


@dataclass(frozen=True, slots=True)
class TerminalReplyState:
    response_id: str
    raw_text: str
    state: ModelOutputState = ModelOutputState.TERMINAL_REPLY


ParsedOutput = ParsedReplyState | FallbackState
ResponseState = ToolRequestState | TerminalReplyState


def item_field(item: Any, field: str) -> Any:
    return item.get(field) if isinstance(item, Mapping) else getattr(item, field, None)


def classify_response(response: Any) -> ResponseState:
    requests: list[ToolRequest] = []
    for item in getattr(response, "output", []) or []:
        if item_field(item, "type") != "function_call":
            continue
        requests.append(
            ToolRequest(
                name=str(item_field(item, "name") or ""),
                call_id=str(item_field(item, "call_id") or item_field(item, "id") or ""),
                raw_arguments=item_field(item, "arguments"),
            )
        )
    response_id = str(getattr(response, "id", "") or "")
    if requests:
        return ToolRequestState(response_id=response_id, requests=tuple(requests))
    return TerminalReplyState(
        response_id=response_id,
        raw_text=str(getattr(response, "output_text", "") or "").strip(),
    )


def strip_markdown_fence(raw: str) -> str:
    match = re.fullmatch(
        r"```(?:json|text)?\s*(.*?)\s*```",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return match.group(1).strip() if match else raw


def normalize_labeled_value(raw: str) -> str:
    return re.sub(
        r"^\s*(?:led|command|code)\s*[:=-]\s*",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()


def clean_reply(raw: str) -> str:
    cleaned_lines: list[str] = []
    for line in raw.splitlines():
        cleaned = re.sub(
            r"^\s*(?:reply|answer|text)\s*[:=-]\s*",
            "",
            line,
            flags=re.IGNORECASE,
        )
        if re.fullmatch(
            r"\s*(?:led|command|code)\s*[:=-]?\s*",
            cleaned,
            flags=re.IGNORECASE,
        ):
            continue
        cleaned_lines.append(cleaned.rstrip())
    cleaned = "\n".join(cleaned_lines).strip()
    return re.sub(
        r"^(?:led|command|code)\s*[:=-]\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()


def parse_json_reply(raw: str) -> tuple[str, str] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    command = str(payload.get("command") or payload.get("code") or "").strip().upper()
    if command not in LED_COMMAND_CODES:
        return None
    reply = str(
        payload.get("reply") or payload.get("answer") or payload.get("text") or ""
    ).strip()
    return command, reply or raw


def parse_embedded_json_reply(raw: str) -> tuple[str, str] | None:
    start = raw.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(raw)):
            character = raw[index]
            if escape:
                escape = False
                continue
            if character == "\\" and in_string:
                escape = True
                continue
            if character == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    parsed = parse_json_reply(raw[start : index + 1])
                    if parsed:
                        return parsed
                    break
        start = raw.find("{", start + 1)
    return None


def parse_model_output(raw: str) -> ParsedOutput:
    stripped = strip_markdown_fence(raw.strip())
    parsed_json = parse_json_reply(stripped)
    if parsed_json:
        return ParsedReplyState(
            raw_text=raw,
            parsed=ParsedModelReply.from_legacy_tuple(parsed_json),
            strategy="json",
        )
    embedded_json = parse_embedded_json_reply(stripped)
    if embedded_json:
        return ParsedReplyState(
            raw_text=raw,
            parsed=ParsedModelReply.from_legacy_tuple(embedded_json),
            strategy="embedded_json",
        )

    lines = stripped.splitlines()
    first_index = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_index is None:
        return FallbackState(
            raw_text=raw,
            parsed=ParsedModelReply(command="BS", reply=stripped),
            reason="empty",
        )

    first = normalize_labeled_value(lines[first_index].strip())
    if first.upper() in LED_COMMAND_CODES:
        return ParsedReplyState(
            raw_text=raw,
            parsed=ParsedModelReply(
                command=first.upper(),
                reply=clean_reply("\n".join(lines[first_index + 1 :])) or stripped,
            ),
            strategy="leading_command",
        )

    command_pattern = r"\b(GS|GP|GC|RS|RF|YP|BS|PS|PP)\b"
    match = re.search(command_pattern, stripped.upper())
    if match:
        command = match.group(1)
        cleaned = clean_reply(re.sub(command_pattern, "", stripped, count=1))
        cleaned = re.sub(
            r"\b(?:led|command|code)\s*[:=-]\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
        return ParsedReplyState(
            raw_text=raw,
            parsed=ParsedModelReply(command=command, reply=cleaned or stripped),
            strategy="embedded_command",
        )

    return FallbackState(
        raw_text=raw,
        parsed=ParsedModelReply(command="BS", reply=stripped),
        reason="unknown_command",
    )


def parse_model_reply(raw: str) -> ParsedModelReply:
    return parse_model_output(raw).parsed


def parse_llm_response(raw: str) -> tuple[str, str]:
    return parse_model_reply(raw).to_legacy_tuple()
