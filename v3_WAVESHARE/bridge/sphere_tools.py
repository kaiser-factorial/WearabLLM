"""Model-facing tools for memory, research, and cross-body expression."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from action_queue import validate_device_id
from household_memory import MEMORY_KINDS


ACTIVE_BODY_IDS = {"wearabllm-esp32", "wearabllm-android", "web-console"}
STATUS_BODY_IDS = {*ACTIVE_BODY_IDS, "wearabllm-wearable"}
LED_COMMANDS = ["GS", "GP", "GC", "RS", "RF", "YP", "BS", "PS", "PP"]
EXPRESSION_CHANNELS = ["visual", "display", "audio"]
MEMORY_WRITE_RE = re.compile(
    r"\b(?:remember|save (?:this|that)|keep (?:this|that) in mind|note that|don't forget)\b",
    re.IGNORECASE,
)
MEMORY_CORRECT_RE = re.compile(
    r"\b(?:correct|update|replace|actually|no longer|instead)\b",
    re.IGNORECASE,
)
MEMORY_FORGET_RE = re.compile(
    r"\b(?:forget|delete|remove|erase)\b.*\b(?:memory|remembered|fact|preference|that|this)\b",
    re.IGNORECASE,
)
BODY_ACTION_RE = re.compile(
    r"\b(?:send|tell|say|speak|play|announce|show|display|light|color|colour)\b",
    re.IGNORECASE,
)
MEMORY_CORRECTION_ROUTE_RE = re.compile(
    r"(?:\b(?:correct|update|replace)\b.{0,160}\b(?:memory|remembered|fact|preference|instruction)\b)"
    r"|(?:\b(?:memory|remembered|fact|preference|instruction)\b.{0,160}"
    r"\b(?:correct|update|replace|actually|no longer|instead)\b)",
    re.IGNORECASE,
)
EXPLICIT_WEB_SEARCH_RE = re.compile(
    r"\b(?:web\s*search|search\s+(?:the\s+)?(?:web|internet)|"
    r"browse\s+(?:the\s+)?(?:web|internet)|google\s+(?:it|this|that|for)|"
    r"look\s+(?:it|this|that)\s+up\s+(?:online|on\s+the\s+web))\b",
    re.IGNORECASE,
)
CURRENT_WEB_INFO_RE = re.compile(
    r"\b(?:latest|currently|current|today|tonight|tomorrow|yesterday|recent|"
    r"release date|version|availability)\b",
    re.IGNORECASE,
)
INHERENTLY_LIVE_INFO_RE = re.compile(
    r"\b(?:news|weather|forecast|temperature|price|stock price|score|standings|"
    r"schedule|in stock|open now)\b",
    re.IGNORECASE,
)
INFORMATION_REQUEST_RE = re.compile(
    r"(?:\?|^\s*(?:who|what|when|where|why|how|is|are|can|could|will|would|"
    r"tell me|find|check)\b)",
    re.IGNORECASE,
)
PERSONAL_CONTACT_CLAIM_RE = re.compile(
    r"\b(?:(?:my|our)\s+(?:(?:home|mailing|delivery|street)\s+)?address|"
    r"i\s+live\s+at|(?:my|our)\s+(?:phone|mobile|cell|email|e-mail)|"
    r"(?:call|text|email|contact|reach)\s+me\s+at)\b",
    re.IGNORECASE,
)
AFFIRMATIVE_RE = re.compile(
    r"\b(?:yes|yeah|yep|please do|go ahead|save it|remember it|confirm|okay|ok)\b",
    re.IGNORECASE,
)
NEGATIVE_RE = re.compile(
    r"\b(?:no|nope|do not|don't|cancel|discard|never mind|nevermind)\b",
    re.IGNORECASE,
)
CREDENTIAL_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:password|passcode|pin|api[_ -]?key|access[_ -]?token|auth(?:entication)?[_ -]?token|"
    r"private[_ -]?key|client[_ -]?secret)\s*(?:is|=|:)\s*[^\s,;]{4,}|"
    r"\b(?:sk-(?:proj-)?|hf_|gh[oprsu]_)[A-Za-z0-9_-]{16,}\b",
    re.IGNORECASE,
)
FINANCIAL_ID_RE = re.compile(
    r"\b(?:social security|ssn|routing number|bank account|credit card)\s*(?:is|=|:)?\s*[0-9 -]{4,}|"
    r"\b\d{3}-\d{2}-\d{4}\b",
    re.IGNORECASE,
)
PRECISE_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.' -]{2,80}\s(?:street|st|road|rd|avenue|ave|"
    r"boulevard|blvd|lane|ln|drive|dr|court|ct|way|place|pl)\b",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
MEMORY_MUTATION_TOOL_NAMES = {"memory_remember", "memory_correct", "memory_forget"}
MEMORY_ID_RE = re.compile(r"\b[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}\b", re.IGNORECASE)


TOOL_INSTRUCTIONS = """
Sphere has model tools with these safety boundaries:
- Use web search for current or externally verifiable information. Keep spoken
  prose concise; source links are shown separately by capable visual bodies.
- Search memory when durable household context would materially improve the
  answer. You may remember stable, user-provided identity, preferences, goals,
  routines, relationships, and long-running project facts without requiring a
  magic phrase. Do not save guesses, assistant claims, transient details, or
  whole conversation turns. The bridge blocks credentials and requires a
  bound yes/no confirmation before saving precise contact or address data.
- Use memory_confirm only in direct response to Sphere's pending yes/no memory
  question. Never ask a sensitive-memory confirmation question until
  memory_remember or memory_correct has actually returned
  confirmation_required. Corrections and forgetting still require an explicit
  current-turn request.
- Never claim that a memory was saved, corrected, confirmed, discarded, or
  forgotten unless the corresponding tool result in this turn reports success.
- Private memory-mutation turns are scoped to their required memory tools.
  Do not search the public web during those turns unless the current message
  explicitly asks for both operations.
- Use sphere_status when the user asks what bodies or Sphere services are
  currently available. It is a passive bridge observation, never a physical
  sensor probe or proof that a body rendered an action.
- Use source_list and source_read to inspect Sphere's own build-time source
  bundle. It is an intentional read-only manifest, not arbitrary filesystem
  access; list first when the path is uncertain and read bounded line ranges.
- send_to_body creates an additional, durable expression on explicitly named
  bodies. Never use it for the ordinary reply on the body that is already
  handling the current request, and never infer a broadcast target.
- All bodies receive the same semantic command. They render visual, display,
  and audio channels according to their capabilities and local preferences.
After tools finish, still return exactly the normal two-line LED code + answer.
"""


def function_tools() -> list[dict[str, Any]]:
    kinds = sorted(MEMORY_KINDS)
    return [
        {
            "type": "function",
            "name": "sphere_status",
            "description": (
                "Read a sanitized passive control-plane snapshot of Sphere bodies, services, "
                "heartbeats, and optionally recent delivery acknowledgements. This does not ping hardware."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "target_device_ids": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(STATUS_BODY_IDS)},
                        "maxItems": 4,
                        "description": "Empty means every known Sphere body.",
                    },
                    "include_recent_actions": {"type": "boolean"},
                },
                "required": ["target_device_ids", "include_recent_actions"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "memory_search",
            "description": (
                "Search all active, unexpired private memories for the current household principal. "
                "Use this to verify a prior save; do not infer save status from conversation context."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query", "limit"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "memory_remember",
            "description": "Save one safe, stable, user-provided durable memory.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "kind": {"type": "string", "enum": kinds},
                    "content": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "importance": {"type": "integer", "minimum": 1, "maximum": 5},
                    "expires_at": {"type": "string", "description": "ISO timestamp or empty string."},
                },
                "required": ["subject", "kind", "content", "tags", "importance", "expires_at"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "memory_confirm",
            "description": (
                "Resolve Sphere's one pending sensitive-memory confirmation after the user answers yes or no."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"save": {"type": "boolean"}},
                "required": ["save"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "memory_correct",
            "description": "Supersede a specific memory after an explicit user correction.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "subject": {"type": "string", "description": "Empty to preserve the current subject."},
                    "kind": {"type": "string", "enum": ["", *kinds]},
                    "content": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["memory_id", "subject", "kind", "content", "tags"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "memory_forget",
            "description": "Mark one specific memory forgotten after an explicit user deletion request.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"memory_id": {"type": "string"}},
                "required": ["memory_id"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "source_list",
            "description": (
                "List files and directories in Sphere's read-only, build-time source manifest. "
                "An empty path lists the manifest root."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "recursive": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["path", "recursive", "limit"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "source_read",
            "description": (
                "Read a bounded line range from one file in Sphere's read-only source manifest."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "start_line": {"type": "integer", "minimum": 1},
                    "line_count": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["path", "start_line", "line_count"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "send_to_body",
            "description": (
                "Queue an additional semantic expression for one or more explicitly named Sphere bodies. "
                "Use the same command and channels across devices; each device owns its renderer."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "target_device_ids": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(ACTIVE_BODY_IDS)},
                        "minItems": 1,
                        "maxItems": 3,
                    },
                    "text": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "command": {"type": "string", "enum": LED_COMMANDS},
                    "channels": {
                        "type": "array",
                        "items": {"type": "string", "enum": EXPRESSION_CHANNELS},
                        "minItems": 1,
                        "maxItems": 3,
                    },
                    "expires_in_seconds": {"type": "integer", "minimum": 15, "maximum": 86400},
                },
                "required": ["target_device_ids", "text", "command", "channels", "expires_in_seconds"],
                "additionalProperties": False,
            },
        },
    ]


def memory_mutation_tools_for_turn(user_transcript: str) -> set[str]:
    """Return the narrow memory workflow explicitly authorized by this turn."""
    names: set[str] = set()
    if MEMORY_WRITE_RE.search(user_transcript):
        names.add("memory_remember")
    if MEMORY_CORRECTION_ROUTE_RE.search(user_transcript):
        names.update(("memory_search", "memory_correct"))
    if MEMORY_FORGET_RE.search(user_transcript):
        names.update(("memory_search", "memory_forget"))
    return names


def forced_memory_mutation_tool_for_turn(user_transcript: str) -> str | None:
    """Choose the first auditable tool for an explicit private-memory mutation."""
    names = memory_mutation_tools_for_turn(user_transcript)
    if "memory_remember" in names:
        return "memory_remember"
    has_id = bool(MEMORY_ID_RE.search(user_transcript))
    if "memory_correct" in names:
        return "memory_correct" if has_id else "memory_search"
    if "memory_forget" in names:
        return "memory_forget" if has_id else "memory_search"
    return None


def explicit_web_search_requested(user_transcript: str) -> bool:
    """Require current-turn web intent before mixing public search with private mutation."""
    return bool(EXPLICIT_WEB_SEARCH_RE.search(user_transcript))


def web_search_requested_for_turn(user_transcript: str) -> bool:
    """Expose public search only for explicit or clearly time-sensitive requests."""
    return bool(
        EXPLICIT_WEB_SEARCH_RE.search(user_transcript)
        or INHERENTLY_LIVE_INFO_RE.search(user_transcript)
        or (
            CURRENT_WEB_INFO_RE.search(user_transcript)
            and INFORMATION_REQUEST_RE.search(user_transcript)
        )
    )


def memory_sensitivity(text: str) -> tuple[list[str], list[str]]:
    """Return hard-blocked and confirmation-required sensitivity categories."""
    blocked: list[str] = []
    confirm: list[str] = []
    if CREDENTIAL_RE.search(text):
        blocked.append("credentials")
    if FINANCIAL_ID_RE.search(text):
        blocked.append("financial_or_government_id")
    if PRECISE_ADDRESS_RE.search(text):
        confirm.append("precise_address")
    if PHONE_RE.search(text):
        confirm.append("phone_number")
    if EMAIL_RE.search(text):
        confirm.append("email_address")
    return blocked, confirm


def memory_confirmation_decision_for_turn(user_transcript: str) -> bool | None:
    """Interpret a pending confirmation answer, prioritizing an explicit negative."""
    if NEGATIVE_RE.search(user_transcript):
        return False
    if AFFIRMATIVE_RE.search(user_transcript):
        return True
    return None


def sensitive_memory_candidate_for_turn(user_transcript: str) -> bool:
    """Identify a concrete personal contact/address claim that must be staged first."""
    blocked, confirm = memory_sensitivity(user_transcript)
    return bool(confirm and not blocked and PERSONAL_CONTACT_CLAIM_RE.search(user_transcript))


class PendingMemoryConfirmationStore:
    """Hold one short-lived sensitive candidate so a later yes/no is bound to it."""

    def __init__(self, *, ttl_seconds: int = 300, clock: Any = time.monotonic) -> None:
        self.ttl_seconds = max(30, min(int(ttl_seconds), 900))
        self.clock = clock
        self.lock = threading.Lock()
        self._pending: dict[str, Any] | None = None

    def stage(
        self,
        arguments: dict[str, Any],
        *,
        source_device_id: str,
        sensitive_categories: list[str],
        operation: str = "remember",
    ) -> None:
        with self.lock:
            self._pending = {
                "arguments": dict(arguments),
                "source_device_id": source_device_id,
                "sensitive_categories": list(sensitive_categories),
                "operation": operation,
                "expires_at": self.clock() + self.ttl_seconds,
            }

    def consume(self) -> dict[str, Any] | None:
        with self.lock:
            pending = self._pending
            self._pending = None
        if not pending or float(pending["expires_at"]) <= self.clock():
            return None
        return pending

    def has_pending(self) -> bool:
        with self.lock:
            pending = self._pending
            if pending and float(pending["expires_at"]) > self.clock():
                return True
            self._pending = None
            return False


class SphereToolExecutor:
    def __init__(
        self,
        *,
        memory_store: Any,
        action_queue: Any,
        status_provider: Any = None,
        source_store: Any = None,
        pending_memory_confirmations: PendingMemoryConfirmationStore | None = None,
        origin_device_id: str,
        user_transcript: str,
    ) -> None:
        self.memory_store = memory_store
        self.action_queue = action_queue
        self.status_provider = status_provider
        self.source_store = source_store
        self.pending_memory_confirmations = pending_memory_confirmations
        self.origin_device_id = validate_device_id(origin_device_id)
        self.user_transcript = user_transcript.strip()

    def execute(self, name: str, arguments: dict[str, Any], *, call_id: str) -> dict[str, Any]:
        if name == "sphere_status":
            if not self.status_provider:
                raise RuntimeError("Sphere status is unavailable")
            targets: list[str] = []
            for raw_target in arguments.get("target_device_ids", []):
                target = validate_device_id(str(raw_target))
                if target not in STATUS_BODY_IDS:
                    raise ValueError(f"Unknown Sphere body: {target}")
                if target not in targets:
                    targets.append(target)
            return {
                "ok": True,
                **self.status_provider(
                    targets,
                    include_recent_actions=bool(arguments.get("include_recent_actions", False)),
                ),
            }
        if name == "memory_search":
            store = self._require_memory_store()
            return {
                "ok": True,
                "memories": store.search(
                    str(arguments.get("query", "")),
                    subject="",
                    kinds=[],
                    limit=int(arguments.get("limit", 5)),
                ),
            }
        if name == "memory_remember":
            content = str(arguments.get("content", ""))
            blocked, confirm = memory_sensitivity(f"{self.user_transcript}\n{content}")
            if blocked:
                raise PermissionError(
                    "Memory not saved: credentials and financial or government identifiers "
                    "cannot be stored in Sphere memory."
                )
            if confirm:
                if not self.pending_memory_confirmations:
                    raise RuntimeError("Sensitive-memory confirmation is unavailable")
                self.pending_memory_confirmations.stage(
                    arguments,
                    source_device_id=self.origin_device_id,
                    sensitive_categories=confirm,
                    operation="remember",
                )
                return {
                    "ok": True,
                    "saved": False,
                    "confirmation_required": True,
                    "sensitive_categories": confirm,
                    "memory_preview": self._memory_preview(arguments),
                }
            return self._remember(arguments, source_device_id=self.origin_device_id)
        if name == "memory_confirm":
            save = bool(arguments.get("save"))
            decision = memory_confirmation_decision_for_turn(self.user_transcript)
            if decision is None or decision is not save:
                raise PermissionError(
                    "The user's current answer does not match this memory confirmation decision."
                )
            if not self.pending_memory_confirmations:
                raise RuntimeError("Sensitive-memory confirmation is unavailable")
            pending = self.pending_memory_confirmations.consume()
            if not pending:
                raise LookupError("There is no pending sensitive memory to confirm")
            if not save:
                return {
                    "ok": True,
                    "saved": False,
                    "confirmed": False,
                    "memory_preview": self._memory_preview(dict(pending["arguments"])),
                }
            operation = str(pending.get("operation", "remember"))
            if operation == "correct":
                result = self._correct(
                    dict(pending["arguments"]),
                    source_device_id=str(pending["source_device_id"]),
                )
            else:
                result = self._remember(
                    dict(pending["arguments"]),
                    source_device_id=str(pending["source_device_id"]),
                )
            result["confirmed"] = True
            return result
        if name == "memory_correct":
            self._require_intent(MEMORY_CORRECT_RE, "The user did not explicitly correct a memory.")
            content = str(arguments.get("content", ""))
            blocked, confirm = memory_sensitivity(f"{self.user_transcript}\n{content}")
            if blocked:
                raise PermissionError(
                    "Memory not corrected: credentials and financial or government identifiers "
                    "cannot be stored in Sphere memory."
                )
            if confirm:
                if not self.pending_memory_confirmations:
                    raise RuntimeError("Sensitive-memory confirmation is unavailable")
                self.pending_memory_confirmations.stage(
                    arguments,
                    source_device_id=self.origin_device_id,
                    sensitive_categories=confirm,
                    operation="correct",
                )
                return {
                    "ok": True,
                    "saved": False,
                    "confirmation_required": True,
                    "sensitive_categories": confirm,
                    "memory_preview": self._memory_preview(arguments),
                }
            return self._correct(arguments, source_device_id=self.origin_device_id)
        if name == "memory_forget":
            self._require_intent(MEMORY_FORGET_RE, "The user did not explicitly ask Sphere to forget a memory.")
            store = self._require_memory_store()
            return {"ok": True, "memory": store.forget(str(arguments.get("memory_id", "")))}
        if name == "source_list":
            store = self._require_source_store()
            return {
                "ok": True,
                "entries": store.list(
                    str(arguments.get("path", "")),
                    recursive=bool(arguments.get("recursive", False)),
                    limit=int(arguments.get("limit", 100)),
                ),
            }
        if name == "source_read":
            store = self._require_source_store()
            return {
                "ok": True,
                "file": store.read(
                    str(arguments.get("path", "")),
                    start_line=int(arguments.get("start_line", 1)),
                    line_count=int(arguments.get("line_count", 120)),
                ),
            }
        if name == "send_to_body":
            self._require_intent(BODY_ACTION_RE, "The user did not explicitly request a body action.")
            return self._send_to_body(arguments, call_id=call_id)
        raise ValueError(f"Unknown Sphere tool: {name}")

    def _send_to_body(self, arguments: dict[str, Any], *, call_id: str) -> dict[str, Any]:
        targets: list[str] = []
        for raw_target in arguments.get("target_device_ids", []):
            target = validate_device_id(str(raw_target))
            if target not in ACTIVE_BODY_IDS:
                raise ValueError(f"Unsupported or inactive target body: {target}")
            if target not in targets:
                targets.append(target)
        if not targets:
            raise ValueError("At least one explicit target body is required")
        text = " ".join(str(arguments.get("text", "")).split()).strip()
        if not text:
            raise ValueError("Expression text is required")
        command = str(arguments.get("command", "BS")).strip().upper()
        channels = [str(value).strip().lower() for value in arguments.get("channels", [])]
        expires_in = max(15, min(int(arguments.get("expires_in_seconds", 300)), 86400))
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
        actions = []
        for target in targets:
            digest = hashlib.sha256(f"{call_id}:{target}".encode("utf-8")).hexdigest()[:24]
            action, created = self.action_queue.create(
                origin_device_id=self.origin_device_id,
                target_device_id=target,
                transcript=self.user_transcript,
                command=command,
                reply=text,
                idempotency_key=f"sphere-tool-{digest}",
                expression={"command": command, "text": text, "channels": channels},
                expires_at=expires_at,
            )
            actions.append({"action": action, "created": created})
        return {"ok": True, "actions": actions}

    def _require_memory_store(self) -> Any:
        if not self.memory_store:
            raise RuntimeError("Rich household memory is not configured")
        return self.memory_store

    def _require_source_store(self) -> Any:
        if not self.source_store:
            raise RuntimeError("Sphere source code is not configured")
        return self.source_store

    def _remember(self, arguments: dict[str, Any], *, source_device_id: str) -> dict[str, Any]:
        store = self._require_memory_store()
        memory, created = store.remember(
            subject=str(arguments.get("subject", "principal")),
            kind=str(arguments.get("kind", "fact")),
            content=str(arguments.get("content", "")),
            tags=[str(value) for value in arguments.get("tags", [])],
            importance=int(arguments.get("importance", 3)),
            confidence=1.0,
            source_device_id=source_device_id,
            expires_at=str(arguments.get("expires_at", "")) or None,
        )
        return {"ok": True, "saved": True, "created": created, "memory": memory}

    def _correct(self, arguments: dict[str, Any], *, source_device_id: str) -> dict[str, Any]:
        store = self._require_memory_store()
        memory = store.correct(
            str(arguments.get("memory_id", "")),
            subject=str(arguments.get("subject", "")),
            kind=str(arguments.get("kind", "")),
            content=str(arguments.get("content", "")),
            tags=[str(value) for value in arguments.get("tags", [])],
            source_device_id=source_device_id,
        )
        return {"ok": True, "saved": True, "memory": memory}

    @staticmethod
    def _memory_preview(arguments: dict[str, Any]) -> str:
        return " ".join(str(arguments.get("content", "")).split()).strip()[:160]

    def _require_intent(self, pattern: re.Pattern[str], message: str) -> None:
        if not pattern.search(self.user_transcript):
            raise PermissionError(message)


def parse_function_arguments(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError("Tool arguments were not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Tool arguments must be a JSON object")
    return value
