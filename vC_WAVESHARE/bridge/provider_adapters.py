"""Narrow model, embedding, STT, and TTS adapters for bridge composition."""

from __future__ import annotations

import base64
import json
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class ModelProvider(Protocol):
    def generate_text(
        self,
        instructions: str,
        input_messages: Sequence[dict[str, str]],
        *,
        model: str,
        max_output_tokens: int,
    ) -> str: ...

    def create_response(self, **options: Any) -> Any: ...


class SpeechToTextProvider(Protocol):
    def transcribe(self, wav_bytes: bytes, *, model: str) -> str: ...


class TextToSpeechProvider(Protocol):
    def synthesize(
        self,
        text: str,
        *,
        model: str,
        voice: str,
        instructions: str,
    ) -> bytes: ...


def create_provider_client(
    factory: Callable[..., Any] | None,
    provider: str,
    *,
    environment: Mapping[str, str],
) -> Any | None:
    if factory is None:
        return None
    if provider == "openai":
        try:
            return factory()
        except Exception:
            if environment.get("OPENAI_API_KEY"):
                raise
            return None
    if provider == "openrouter":
        try:
            return factory(
                api_key=environment.get("OPENROUTER_API_KEY", ""),
                base_url=OPENROUTER_BASE_URL,
            )
        except Exception:
            if environment.get("OPENROUTER_API_KEY"):
                raise
            return None
    raise ValueError(f"Unsupported model provider: {provider}")


def validated_openai_client(
    factory: Callable[..., Any],
    api_key: str,
) -> tuple[Any, list[str]]:
    """Construct a candidate client and prove it can discover at least one model."""
    client = factory(api_key=api_key)
    model_ids = SDKProviderAdapter(client, "openai").model_ids()
    if not model_ids:
        raise RuntimeError("OpenAI returned no available models for this key")
    return client, model_ids


class SDKProviderAdapter:
    """OpenAI-compatible SDK adapter selected by provider name."""

    def __init__(self, client: Any, provider: str) -> None:
        if provider not in ("openai", "openrouter"):
            raise ValueError(f"Unsupported model provider: {provider}")
        self.client = client
        self.provider = provider

    def generate_text(
        self,
        instructions: str,
        input_messages: Sequence[dict[str, str]],
        *,
        model: str,
        max_output_tokens: int,
    ) -> str:
        if self.provider == "openai":
            response = self.client.responses.create(
                model=model,
                instructions=instructions,
                input=list(input_messages),
                max_output_tokens=max_output_tokens,
            )
            return str(response.output_text).strip()
        response = self.client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": instructions}, *input_messages],
            max_tokens=max_output_tokens,
        )
        content = response.choices[0].message.content if response.choices else ""
        return str(content or "").strip()

    def create_response(self, **options: Any) -> Any:
        if self.provider != "openai":
            raise RuntimeError("Responses tools require the OpenAI provider")
        return self.client.responses.create(**options)

    def embedding(
        self,
        text: str,
        *,
        model: str,
        dimensions: int,
    ) -> list[float]:
        if self.provider != "openai":
            raise RuntimeError("Household-memory embeddings require the OpenAI provider")
        response = self.client.embeddings.create(
            model=model,
            input=text,
            dimensions=dimensions,
            encoding_format="float",
        )
        data = getattr(response, "data", None)
        if not data:
            raise RuntimeError("OpenAI returned no household-memory embedding")
        embedding = getattr(data[0], "embedding", None)
        if not isinstance(embedding, list):
            raise RuntimeError("OpenAI returned an invalid household-memory embedding")
        return embedding

    def model_ids(self) -> list[str]:
        payload = self.client.models.list()
        rows = getattr(payload, "data", payload)
        if not isinstance(rows, (list, tuple)):
            return []
        identifiers = {
            str(
                getattr(row, "id", row.get("id", "") if isinstance(row, dict) else "")
            ).strip()
            for row in rows
        }
        return sorted(identifier for identifier in identifiers if identifier)

    def transcribe(self, wav_bytes: bytes, *, model: str) -> str:
        result = self.client.audio.transcriptions.create(
            model=model,
            file=("wearabllm-capture.wav", wav_bytes, "audio/wav"),
            response_format="text",
        )
        return str(result).strip()

    def synthesize(
        self,
        text: str,
        *,
        model: str,
        voice: str,
        instructions: str,
    ) -> bytes:
        request: dict[str, Any] = {
            "model": model,
            "voice": voice,
            "input": text,
            "response_format": "wav",
        }
        if self.provider == "openai":
            request["instructions"] = instructions
        response = self.client.audio.speech.create(**request)
        if hasattr(response, "read"):
            return bytes(response.read())
        if isinstance(response, bytes):
            return response
        raise RuntimeError("Unexpected TTS response type")


class OpenRouterTranscriber:
    def __init__(
        self,
        api_key: str,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.api_key = api_key
        self.opener = opener

    def transcribe(self, wav_bytes: bytes, *, model: str) -> str:
        payload = {
            "model": model,
            "input_audio": {
                "data": base64.b64encode(wav_bytes).decode("ascii"),
                "format": "wav",
            },
            "language": "en",
        }
        request = urllib.request.Request(
            f"{OPENROUTER_BASE_URL}/audio/transcriptions",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self.opener(request, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"OpenRouter transcription failed ({exc.code}): {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenRouter transcription failed: {exc.reason}") from exc
        transcript = str(result.get("text", "")).strip() if isinstance(result, dict) else ""
        if not transcript:
            raise RuntimeError("OpenRouter transcription returned no text")
        return transcript


class LocalWhisperTranscriber:
    def __init__(self, model_name: str, event_sink: Callable[..., None]) -> None:
        self.model_name = model_name
        self.event_sink = event_sink
        self.model: Any | None = None

    def transcribe(self, wav_bytes: bytes, *, model: str = "") -> str:
        del model
        try:
            import whisper  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "local-whisper selected, but openai-whisper is not installed"
            ) from exc
        if self.model is None:
            self.event_sink("bridge.local_whisper_loading", model=self.model_name)
            self.model = whisper.load_model(self.model_name)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as audio_file:
                audio_file.write(wav_bytes)
                temporary_path = Path(audio_file.name)
            result = self.model.transcribe(str(temporary_path), language="en", fp16=False)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return str(result.get("text", "")).strip()
