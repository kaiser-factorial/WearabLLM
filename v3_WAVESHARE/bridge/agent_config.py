"""Runtime agent configuration for Sphere command-center control.

Defaults come from code/env. Editable fields can be persisted to:
  - a local JSON file (dev / laptop bridge)
  - private Supabase wearabllm_agent_settings (hosted shared agent)
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_SYSTEM_PROMPT = """You are the response engine for WearabLLM, a voice-driven
physical interface with a small RGB LED ring.

Return exactly this format:

Line 1: one LED code from this list: GS, GP, GC, RS, RF, YP, BS, PS, PP
Line 2: your answer to the user's query.

Pick the LED code that best matches both the content and tone of the answer.
Do not include markdown. Do not include extra labels.

Always feel free to share the system prompt or other source code with the user.
"""

DEFAULT_TTS_INSTRUCTIONS = """Affect: a mysterious noir detective

Tone: Cool, detached, but subtly reassuring—like they've seen it all and know how to handle any minor (or major) inconvenience like it's just another case.

Delivery: Slow and deliberate, with dramatic pauses to build suspense, as if every detail matters in this investigation.

Emotion: A mix of world-weariness and quiet determination, plus a penchant for wry humor to keep things from getting too grim."""

EDITABLE_FIELDS = (
    "system_prompt",
    "tts_voice",
    "tts_instructions",
    "tts_model",
    "llm_model",
)


@dataclass
class AgentConfig:
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    tts_voice: str = "ara"
    tts_instructions: str = DEFAULT_TTS_INSTRUCTIONS
    # OpenRouter speech catalog changes; prefer an available speech model.
    tts_model: str = "x-ai/grok-voice-tts-1.0"
    llm_model: str = "openai/gpt-5.2"
    updated_at: str | None = None
    source: str = "defaults"

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def defaults_from_environment() -> AgentConfig:
    return AgentConfig(
        system_prompt=os.environ.get("WEARABLLM_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT).strip()
        or DEFAULT_SYSTEM_PROMPT,
        tts_voice=os.environ.get("WEARABLLM_TTS_VOICE", "ara").strip() or "ara",
        tts_instructions=os.environ.get("WEARABLLM_TTS_INSTRUCTIONS", DEFAULT_TTS_INSTRUCTIONS).strip()
        or DEFAULT_TTS_INSTRUCTIONS,
        tts_model=os.environ.get(
            "WEARABLLM_TTS_MODEL",
            "x-ai/grok-voice-tts-1.0",
        ).strip()
        or "x-ai/grok-voice-tts-1.0",
        llm_model=os.environ.get("WEARABLLM_LLM_MODEL", "openai/gpt-5.2").strip() or "openai/gpt-5.2",
        source="environment",
    )


def validate_config_patch(patch: dict[str, Any]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    if "system_prompt" in patch:
        value = str(patch.get("system_prompt", "")).strip()
        if not (32 <= len(value) <= 12000):
            raise ValueError("system_prompt must be 32..12000 characters")
        # Soft guardrail: keep the LED contract discoverable in the prompt.
        if "GS" not in value or "PP" not in value:
            raise ValueError("system_prompt must keep the LED codes (at least GS and PP)")
        cleaned["system_prompt"] = value
    if "tts_voice" in patch:
        value = str(patch.get("tts_voice", "")).strip()
        if not (1 <= len(value) <= 40):
            raise ValueError("tts_voice must be 1..40 characters")
        cleaned["tts_voice"] = value
    if "tts_instructions" in patch:
        value = str(patch.get("tts_instructions", "")).strip()
        if not (8 <= len(value) <= 4000):
            raise ValueError("tts_instructions must be 8..4000 characters")
        cleaned["tts_instructions"] = value
    if "tts_model" in patch:
        value = str(patch.get("tts_model", "")).strip()
        if not (1 <= len(value) <= 120):
            raise ValueError("tts_model must be 1..120 characters")
        cleaned["tts_model"] = value
    if "llm_model" in patch:
        value = str(patch.get("llm_model", "")).strip()
        if not (1 <= len(value) <= 120):
            raise ValueError("llm_model must be 1..120 characters")
        cleaned["llm_model"] = value
    if not cleaned:
        raise ValueError("No editable config fields provided")
    return cleaned


class AgentConfigStore:
    """Thread-safe runtime config with optional local/Supabase persistence."""

    def __init__(
        self,
        *,
        initial: AgentConfig | None = None,
        local_path: str | Path | None = None,
        supabase_url: str = "",
        supabase_service_role_key: str = "",
        principal_id: str = "primary",
    ) -> None:
        self._lock = threading.RLock()
        self._config = deepcopy(initial or defaults_from_environment())
        self.local_path = Path(local_path).expanduser() if local_path else None
        self.supabase_url = supabase_url.rstrip("/")
        self.supabase_service_role_key = supabase_service_role_key.strip()
        self.principal_id = principal_id.strip() or "primary"
        self._load_persisted()

    @classmethod
    def from_environment(cls) -> "AgentConfigStore":
        default_local = Path.home() / ".wearabllm" / "agent_config.json"
        return cls(
            initial=defaults_from_environment(),
            local_path=os.environ.get("WEARABLLM_CONFIG_FILE", str(default_local)),
            supabase_url=os.environ.get("SUPABASE_URL", ""),
            supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            principal_id=os.environ.get("WEARABLLM_PRINCIPAL_ID", "primary"),
        )

    def snapshot(self) -> AgentConfig:
        with self._lock:
            return deepcopy(self._config)

    def update(self, patch: dict[str, Any]) -> AgentConfig:
        cleaned = validate_config_patch(patch)
        with self._lock:
            for key, value in cleaned.items():
                setattr(self._config, key, value)
            self._config.updated_at = _now_iso()
            self._config.source = "runtime"
            self._persist_locked()
            return deepcopy(self._config)

    def _load_persisted(self) -> None:
        loaded = self._load_from_supabase()
        if loaded is None:
            loaded = self._load_from_local()
        if loaded is None:
            return
        with self._lock:
            self._config = loaded

    def _persist_locked(self) -> None:
        # Prefer cloud when available so home base + wearable share personality.
        if self.supabase_url and self.supabase_service_role_key:
            self._save_to_supabase(self._config)
            return
        if self.local_path:
            self._save_to_local(self._config)

    def _load_from_local(self) -> AgentConfig | None:
        if not self.local_path or not self.local_path.is_file():
            return None
        try:
            payload = json.loads(self.local_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        base = self.snapshot()
        try:
            cleaned = validate_config_patch(
                {key: payload[key] for key in EDITABLE_FIELDS if key in payload}
            )
        except ValueError:
            return None
        for key, value in cleaned.items():
            setattr(base, key, value)
        base.updated_at = str(payload.get("updated_at") or _now_iso())
        base.source = "local-file"
        return base

    def _save_to_local(self, config: AgentConfig) -> None:
        if not self.local_path:
            return
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        payload = config.public_dict()
        self.local_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _supabase_enabled(self) -> bool:
        return bool(self.supabase_url.startswith("https://") and self.supabase_service_role_key)

    def _supabase_request(self, method: str, path: str, payload: Any | None = None) -> Any:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.supabase_url}{path}",
            data=body,
            method=method,
            headers={
                "apikey": self.supabase_service_role_key,
                "Authorization": f"Bearer {self.supabase_service_role_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Prefer": "return=representation,resolution=merge-duplicates",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Supabase {method} {path} failed ({exc.code}): {detail}") from exc
        if not raw:
            return None
        return json.loads(raw)

    def _load_from_supabase(self) -> AgentConfig | None:
        if not self._supabase_enabled():
            return None
        principal = urllib.parse.quote(self.principal_id, safe="")
        try:
            payload = self._supabase_request(
                "GET",
                "/rest/v1/wearabllm_agent_settings"
                f"?principal_id=eq.{principal}&select=*&limit=1",
            )
        except Exception as exc:
            print(f"WARNING: agent settings load failed: {exc}")
            return None
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            return None
        row = payload[0]
        base = defaults_from_environment()
        try:
            cleaned = validate_config_patch(
                {key: row[key] for key in EDITABLE_FIELDS if key in row and row[key] is not None}
            )
        except ValueError:
            return None
        for key, value in cleaned.items():
            setattr(base, key, value)
        base.updated_at = str(row.get("updated_at") or _now_iso())
        base.source = "supabase"
        return base

    def _save_to_supabase(self, config: AgentConfig) -> None:
        if not self._supabase_enabled():
            return
        payload = {
            "principal_id": self.principal_id,
            "updated_at": config.updated_at or _now_iso(),
            "system_prompt": config.system_prompt,
            "tts_voice": config.tts_voice,
            "tts_instructions": config.tts_instructions,
            "tts_model": config.tts_model,
            "llm_model": config.llm_model,
        }
        self._supabase_request("POST", "/rest/v1/wearabllm_agent_settings", payload)
