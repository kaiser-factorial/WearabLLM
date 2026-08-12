"""Typed internal contracts for the WearabLLM bridge.

These contracts deliberately use only the Python standard library. They make
the model, persistence, service, and transport handoffs explicit while keeping
the existing ``/v1`` dictionaries available through narrow compatibility
adapters.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


LED_COMMAND_CODES = frozenset({"GS", "GP", "GC", "RS", "RF", "YP", "BS", "PS", "PP"})


class ContractError(ValueError):
    """Raised when an internal or legacy boundary value violates its schema."""


class PersistenceStatus(str, Enum):
    PENDING = "pending"
    PERSISTED = "persisted"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_CONFIGURED = "not_configured"


def _require_string(value: Any, field_name: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{field_name} must be a string")
    if not allow_empty and not value.strip():
        raise ContractError(f"{field_name} must not be empty")
    return value


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, field_name)


def _require_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{field_name} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ContractError(f"{field_name} keys must be strings")
    return value


def _copy_object(value: Any, field_name: str) -> dict[str, Any]:
    return deepcopy(dict(_require_mapping(value, field_name)))


@dataclass(frozen=True, slots=True)
class PersistenceResult:
    status: PersistenceStatus
    backend: str
    session_id: str | None = None
    error_code: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, PersistenceStatus):
            raise ContractError("persistence status must be a PersistenceStatus")
        _require_string(self.backend, "persistence backend", allow_empty=False)
        _optional_string(self.session_id, "persistence session_id")
        _optional_string(self.error_code, "persistence error_code")
        _optional_string(self.message, "persistence message")
        if self.status is PersistenceStatus.FAILED and not self.error_code:
            raise ContractError("failed persistence result requires error_code")

    @classmethod
    def create(
        cls,
        status: PersistenceStatus | str,
        *,
        backend: str,
        session_id: str | None = None,
    ) -> "PersistenceResult":
        try:
            normalized = status if isinstance(status, PersistenceStatus) else PersistenceStatus(status)
        except ValueError as exc:
            raise ContractError(f"unsupported persistence status: {status}") from exc
        error_code: str | None = None
        message: str | None = None
        if normalized is PersistenceStatus.FAILED:
            error_code = "conversation_write_failed"
            message = (
                "Sphere answered, but this exchange could not be saved. "
                "Copy anything important and retry."
            )
        elif normalized is PersistenceStatus.SKIPPED:
            message = "Conversation persistence is skipped in dry-run mode."
        elif normalized is PersistenceStatus.NOT_CONFIGURED:
            message = "Conversation persistence is not configured."
        return cls(
            status=normalized,
            backend=backend,
            session_id=session_id or None,
            error_code=error_code,
            message=message,
        )

    def with_session(self, session_id: str | None) -> "PersistenceResult":
        return PersistenceResult(
            status=self.status,
            backend=self.backend,
            session_id=session_id or None,
            error_code=self.error_code,
            message=self.message,
        )

    def to_legacy_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status.value,
            "backend": self.backend,
            "session_id": self.session_id,
        }
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        if self.message is not None:
            payload["message"] = self.message
        return payload

    @classmethod
    def from_legacy_dict(cls, payload: Mapping[str, Any]) -> "PersistenceResult":
        source = _require_mapping(payload, "persistence")
        try:
            status = PersistenceStatus(_require_string(source.get("status"), "persistence status"))
        except ValueError as exc:
            raise ContractError(f"unsupported persistence status: {source.get('status')}") from exc
        result = cls(
            status=status,
            backend=_require_string(source.get("backend"), "persistence backend", allow_empty=False),
            session_id=_optional_string(source.get("session_id"), "persistence session_id"),
            error_code=_optional_string(source.get("error_code"), "persistence error_code"),
            message=_optional_string(source.get("message"), "persistence message"),
        )
        if status is PersistenceStatus.FAILED and not result.error_code:
            raise ContractError("failed persistence result requires error_code")
        return result


@dataclass(frozen=True, slots=True)
class SourceReference:
    title: str
    url: str

    def __post_init__(self) -> None:
        _require_string(self.title, "source title")
        _require_string(self.url, "source url", allow_empty=False)

    def to_legacy_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url}

    @classmethod
    def from_legacy_dict(cls, payload: Mapping[str, Any]) -> "SourceReference":
        source = _require_mapping(payload, "source")
        return cls(
            title=_require_string(source.get("title", ""), "source title"),
            url=_require_string(source.get("url"), "source url", allow_empty=False),
        )


@dataclass(frozen=True, slots=True)
class ToolActivity:
    name: str
    ok: bool
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_string(self.name, "tool activity name", allow_empty=False)
        if not isinstance(self.ok, bool):
            raise ContractError("tool activity ok must be boolean")
        _require_string(self.summary, "tool activity summary")
        _require_mapping(self.details, "tool activity details")
        if {"name", "ok", "summary"}.intersection(self.details):
            raise ContractError("tool activity details contain reserved keys")
        object.__setattr__(self, "details", _copy_object(self.details, "tool activity details"))

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "summary": self.summary,
            **deepcopy(self.details),
        }

    @classmethod
    def from_legacy_dict(cls, payload: Mapping[str, Any]) -> "ToolActivity":
        source = _require_mapping(payload, "tool activity")
        ok = source.get("ok")
        if not isinstance(ok, bool):
            raise ContractError("tool activity ok must be boolean")
        return cls(
            name=_require_string(source.get("name"), "tool activity name", allow_empty=False),
            ok=ok,
            summary=_require_string(source.get("summary", ""), "tool activity summary"),
            details={
                key: deepcopy(value)
                for key, value in source.items()
                if key not in {"name", "ok", "summary"}
            },
        )


@dataclass(frozen=True, slots=True)
class ModelToolContext:
    name: str
    arguments: str
    output: str

    def __post_init__(self) -> None:
        _require_string(self.name, "model tool name", allow_empty=False)
        _require_string(self.arguments, "model tool arguments")
        _require_string(self.output, "model tool output")

    def to_legacy_dict(self) -> dict[str, str]:
        return {"name": self.name, "arguments": self.arguments, "output": self.output}

    @classmethod
    def from_legacy_dict(cls, payload: Mapping[str, Any]) -> "ModelToolContext":
        source = _require_mapping(payload, "model tool context")
        return cls(
            name=_require_string(source.get("name"), "model tool name", allow_empty=False),
            arguments=_require_string(source.get("arguments", ""), "model tool arguments"),
            output=_require_string(source.get("output", ""), "model tool output"),
        )


@dataclass(slots=True)
class ModelActivity:
    sources: list[SourceReference] = field(default_factory=list)
    tool_results: list[ToolActivity] = field(default_factory=list)
    model_tool_context: list[ModelToolContext] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.sources, list):
            raise ContractError("model activity sources must be a list")
        if not isinstance(self.tool_results, list):
            raise ContractError("model activity tool_results must be a list")
        if not isinstance(self.model_tool_context, list):
            raise ContractError("model activity model_tool_context must be a list")
        if not all(isinstance(source, SourceReference) for source in self.sources):
            raise ContractError("model activity sources must contain SourceReference values")
        if not all(isinstance(result, ToolActivity) for result in self.tool_results):
            raise ContractError("model activity tool_results must contain ToolActivity values")
        if not all(isinstance(context, ModelToolContext) for context in self.model_tool_context):
            raise ContractError(
                "model activity model_tool_context must contain ModelToolContext values"
            )

    def to_legacy_dict(self, *, include_private: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "sources": [source.to_legacy_dict() for source in self.sources],
            "tool_results": [result.to_legacy_dict() for result in self.tool_results],
        }
        if include_private and self.model_tool_context:
            payload["model_tool_context"] = [
                context.to_legacy_dict() for context in self.model_tool_context
            ]
        return payload

    def to_storage_metadata(self) -> dict[str, Any]:
        payload = self.to_legacy_dict(include_private=True)
        return {key: value for key, value in payload.items() if value}

    @classmethod
    def from_legacy_dict(cls, payload: Mapping[str, Any]) -> "ModelActivity":
        source = _require_mapping(payload, "model activity")
        raw_sources = source.get("sources", [])
        raw_tools = source.get("tool_results", [])
        raw_context = source.get("model_tool_context", [])
        for value, field_name in (
            (raw_sources, "model activity sources"),
            (raw_tools, "model activity tool_results"),
            (raw_context, "model activity model_tool_context"),
        ):
            if not isinstance(value, list):
                raise ContractError(f"{field_name} must be a list")
        return cls(
            sources=[SourceReference.from_legacy_dict(item) for item in raw_sources],
            tool_results=[ToolActivity.from_legacy_dict(item) for item in raw_tools],
            model_tool_context=[ModelToolContext.from_legacy_dict(item) for item in raw_context],
        )


@dataclass(frozen=True, slots=True)
class ParsedModelReply:
    command: str
    reply: str

    def __post_init__(self) -> None:
        if self.command not in LED_COMMAND_CODES:
            raise ContractError(f"invalid model command: {self.command}")
        _require_string(self.reply, "model reply")

    def to_legacy_tuple(self) -> tuple[str, str]:
        return self.command, self.reply

    @classmethod
    def from_legacy_tuple(cls, value: tuple[str, str]) -> "ParsedModelReply":
        if not isinstance(value, tuple) or len(value) != 2:
            raise ContractError("legacy model reply must be a two-item tuple")
        return cls(
            command=_require_string(value[0], "model command", allow_empty=False),
            reply=_require_string(value[1], "model reply"),
        )


@dataclass(frozen=True, slots=True)
class GeneratedModelText:
    raw_text: str
    activity: ModelActivity = field(default_factory=ModelActivity)

    def __post_init__(self) -> None:
        _require_string(self.raw_text, "generated model text")
        if not isinstance(self.activity, ModelActivity):
            raise ContractError("generated model activity must be ModelActivity")

    def to_legacy_tuple(self) -> tuple[str, dict[str, Any]]:
        return self.raw_text, self.activity.to_legacy_dict(include_private=True)


@dataclass(frozen=True, slots=True)
class AssistantResult:
    parsed: ParsedModelReply
    activity: ModelActivity
    persistence: PersistenceResult

    def __post_init__(self) -> None:
        if not isinstance(self.parsed, ParsedModelReply):
            raise ContractError("assistant parsed reply must be ParsedModelReply")
        if not isinstance(self.activity, ModelActivity):
            raise ContractError("assistant activity must be ModelActivity")
        if not isinstance(self.persistence, PersistenceResult):
            raise ContractError("assistant persistence must be PersistenceResult")

    @property
    def command(self) -> str:
        return self.parsed.command

    @property
    def reply(self) -> str:
        return self.parsed.reply

    def to_legacy_metadata(self) -> dict[str, Any]:
        return {
            **self.activity.to_legacy_dict(include_private=True),
            "persistence": self.persistence.to_legacy_dict(),
        }


@dataclass(frozen=True, slots=True)
class QueryInput:
    transcript: str
    device_id: str = "wearabllm-unknown"
    response_device_id: str | None = None
    audio_bytes: int = 0
    saved_wav: Path | None = None
    wav_info: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        _require_string(self.transcript, "query transcript")
        _require_string(self.device_id, "query device_id", allow_empty=False)
        _optional_string(self.response_device_id, "query response_device_id")
        if not isinstance(self.audio_bytes, int) or isinstance(self.audio_bytes, bool) or self.audio_bytes < 0:
            raise ContractError("query audio_bytes must be a non-negative integer")
        if self.saved_wav is not None and not isinstance(self.saved_wav, Path):
            raise ContractError("query saved_wav must be a Path or None")
        if self.wav_info is not None:
            _require_mapping(self.wav_info, "query wav_info")
            object.__setattr__(self, "wav_info", _copy_object(self.wav_info, "query wav_info"))


@dataclass(frozen=True, slots=True)
class QueryResult:
    command: str
    reply: str
    transcript: str
    audio_bytes: int
    saved_wav: str | None
    wav_info: dict[str, Any] | None
    sources: tuple[SourceReference, ...]
    tool_results: tuple[ToolActivity, ...]
    persistence: PersistenceResult

    def __post_init__(self) -> None:
        if self.command not in LED_COMMAND_CODES:
            raise ContractError(f"invalid query command: {self.command}")
        _require_string(self.reply, "query reply")
        _require_string(self.transcript, "query transcript")
        if not isinstance(self.audio_bytes, int) or isinstance(self.audio_bytes, bool) or self.audio_bytes < 0:
            raise ContractError("query audio_bytes must be a non-negative integer")
        _optional_string(self.saved_wav, "query saved_wav")
        if self.wav_info is not None:
            _require_mapping(self.wav_info, "query wav_info")
            object.__setattr__(self, "wav_info", _copy_object(self.wav_info, "query wav_info"))
        if not isinstance(self.sources, tuple):
            raise ContractError("query sources must be a tuple")
        if not isinstance(self.tool_results, tuple):
            raise ContractError("query tool_results must be a tuple")
        if not all(isinstance(source, SourceReference) for source in self.sources):
            raise ContractError("query sources must contain SourceReference values")
        if not all(isinstance(result, ToolActivity) for result in self.tool_results):
            raise ContractError("query tool_results must contain ToolActivity values")
        if not isinstance(self.persistence, PersistenceResult):
            raise ContractError("query persistence must be PersistenceResult")

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "reply": self.reply,
            "transcript": self.transcript,
            "audio_bytes": self.audio_bytes,
            "saved_wav": self.saved_wav,
            "wav_info": deepcopy(self.wav_info),
            "sources": [source.to_legacy_dict() for source in self.sources],
            "tool_results": [result.to_legacy_dict() for result in self.tool_results],
            "persistence": self.persistence.to_legacy_dict(),
        }

    @classmethod
    def from_legacy_dict(cls, payload: Mapping[str, Any]) -> "QueryResult":
        source = _require_mapping(payload, "query result")
        audio_bytes = source.get("audio_bytes")
        if not isinstance(audio_bytes, int) or isinstance(audio_bytes, bool) or audio_bytes < 0:
            raise ContractError("query audio_bytes must be a non-negative integer")
        raw_sources = source.get("sources")
        raw_tools = source.get("tool_results")
        if not isinstance(raw_sources, list):
            raise ContractError("query sources must be a list")
        if not isinstance(raw_tools, list):
            raise ContractError("query tool_results must be a list")
        wav_info = source.get("wav_info")
        return cls(
            command=_require_string(source.get("command"), "query command", allow_empty=False),
            reply=_require_string(source.get("reply"), "query reply"),
            transcript=_require_string(source.get("transcript"), "query transcript"),
            audio_bytes=audio_bytes,
            saved_wav=_optional_string(source.get("saved_wav"), "query saved_wav"),
            wav_info=None if wav_info is None else _copy_object(wav_info, "query wav_info"),
            sources=tuple(SourceReference.from_legacy_dict(item) for item in raw_sources),
            tool_results=tuple(ToolActivity.from_legacy_dict(item) for item in raw_tools),
            persistence=PersistenceResult.from_legacy_dict(
                _require_mapping(source.get("persistence"), "query persistence")
            ),
        )


@dataclass(frozen=True, slots=True)
class InteractionInput:
    transcript: str
    origin_device_id: str
    target_device_id: str
    idempotency_key: str = ""
    response_device_id: str | None = None

    def __post_init__(self) -> None:
        _require_string(self.transcript, "interaction transcript", allow_empty=False)
        _require_string(self.origin_device_id, "interaction origin_device_id", allow_empty=False)
        _require_string(self.target_device_id, "interaction target_device_id", allow_empty=False)
        _require_string(self.idempotency_key, "interaction idempotency_key")
        _optional_string(self.response_device_id, "interaction response_device_id")


@dataclass(frozen=True, slots=True)
class InteractionResult:
    query: QueryResult
    action: dict[str, Any]
    action_created: bool

    def __post_init__(self) -> None:
        if not isinstance(self.query, QueryResult):
            raise ContractError("interaction query must be QueryResult")
        _require_mapping(self.action, "interaction action")
        object.__setattr__(self, "action", _copy_object(self.action, "interaction action"))
        if not isinstance(self.action_created, bool):
            raise ContractError("interaction action_created must be boolean")

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            **self.query.to_legacy_dict(),
            "action": deepcopy(self.action),
            "action_created": self.action_created,
        }
