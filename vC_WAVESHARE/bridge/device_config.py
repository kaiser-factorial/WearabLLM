"""Validated planning and execution for privileged firmware configuration."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


BSSID_RE = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$", re.IGNORECASE)


def _optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError("optional config flags must be boolean")


def _optional_int(value: Any, name: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple)):
        raise ValueError(f"{name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _clean_secret(value: Any, name: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    cleaned = value.strip() if name == "ssid" else value
    if not cleaned:
        raise ValueError("ssid and password are required")
    if len(cleaned) > max_length or any(ord(char) < 32 for char in cleaned):
        raise ValueError(f"{name} contains unsafe characters or is too long")
    return cleaned


@dataclass(frozen=True, slots=True)
class DeviceWifiConfig:
    ssid: str
    password: str
    bssid: str = ""
    ptt_gpio: int | None = None
    ptt_active_level: int | None = None
    ptt_debounce_ms: int | None = None
    ptt_pull: str = ""
    audio_out_enabled: bool | None = None
    audio_out_volume: int | None = None
    tts_enabled: bool | None = None
    tts_max_bytes: int | None = None
    led_self_test: bool | None = None
    display_enabled: bool | None = None
    display_self_test: bool | None = None

    def public_options(self) -> dict[str, Any]:
        return {
            "ptt_gpio": self.ptt_gpio,
            "ptt_active_level": self.ptt_active_level,
            "ptt_debounce_ms": self.ptt_debounce_ms,
            "ptt_pull": self.ptt_pull or None,
            "audio_out_enabled": self.audio_out_enabled,
            "audio_out_volume": self.audio_out_volume,
            "tts_enabled": self.tts_enabled,
            "tts_max_bytes": self.tts_max_bytes,
            "led_self_test": self.led_self_test,
            "display_enabled": self.display_enabled,
            "display_self_test": self.display_self_test,
        }


def normalize_device_wifi_input(payload: Mapping[str, Any]) -> DeviceWifiConfig:
    if not isinstance(payload, Mapping):
        raise ValueError("Device config must be an object")
    ssid = _clean_secret(payload.get("ssid"), "ssid", max_length=32)
    password = _clean_secret(payload.get("password"), "password", max_length=256)
    bssid_value = payload.get("bssid", "")
    if not isinstance(bssid_value, str):
        raise ValueError("bssid must be a string")
    bssid = bssid_value.strip().lower()
    if bssid and not BSSID_RE.fullmatch(bssid):
        raise ValueError("Wi-Fi BSSID must look like 02:00:00:00:00:01")

    config = DeviceWifiConfig(
        ssid=ssid,
        password=password,
        bssid=bssid,
        ptt_gpio=_optional_int(payload.get("ptt_gpio"), "ptt_gpio"),
        ptt_active_level=_optional_int(payload.get("ptt_active_level"), "ptt_active_level"),
        ptt_debounce_ms=_optional_int(payload.get("ptt_debounce_ms"), "ptt_debounce_ms"),
        ptt_pull=str(payload.get("ptt_pull", "")).strip().lower(),
        audio_out_enabled=_optional_bool(payload.get("audio_out_enabled")),
        audio_out_volume=_optional_int(payload.get("audio_out_volume"), "audio_out_volume"),
        tts_enabled=_optional_bool(payload.get("tts_enabled")),
        tts_max_bytes=_optional_int(payload.get("tts_max_bytes"), "tts_max_bytes"),
        led_self_test=_optional_bool(payload.get("led_self_test")),
        display_enabled=_optional_bool(payload.get("display_enabled")),
        display_self_test=_optional_bool(payload.get("display_self_test")),
    )
    validate_device_wifi_config(config)
    return config


def validate_device_wifi_config(config: DeviceWifiConfig) -> None:
    if config.ptt_gpio is not None and not 0 <= config.ptt_gpio <= 48:
        raise ValueError("ptt_gpio must be between 0 and 48")
    if config.ptt_active_level is not None and config.ptt_active_level not in (0, 1):
        raise ValueError("ptt_active_level must be 0 or 1")
    if config.ptt_debounce_ms is not None and not 0 <= config.ptt_debounce_ms <= 250:
        raise ValueError("ptt_debounce_ms must be between 0 and 250")
    if config.ptt_pull and config.ptt_pull not in ("none", "up", "down"):
        raise ValueError("ptt_pull must be one of: none, up, down")
    if config.audio_out_volume is not None and not 0 <= config.audio_out_volume <= 100:
        raise ValueError("audio_out_volume must be between 0 and 100")
    if config.tts_max_bytes is not None and not 4096 <= config.tts_max_bytes <= 1048576:
        raise ValueError("tts_max_bytes must be between 4096 and 1048576")


def build_device_config_command(config: DeviceWifiConfig, helper_path: Path) -> list[str]:
    command = [str(helper_path)]
    values = (
        ("--ptt-gpio", config.ptt_gpio),
        ("--ptt-active-level", config.ptt_active_level),
        ("--ptt-debounce-ms", config.ptt_debounce_ms),
        ("--ptt-pull", config.ptt_pull or None),
        ("--audio-out-volume", config.audio_out_volume),
        ("--tts-max-bytes", config.tts_max_bytes),
    )
    for flag, value in values:
        if value is not None:
            command.extend([flag, str(value)])
    toggles = (
        ("audio_out_enabled", "--enable-audio-out", "--disable-audio-out"),
        ("tts_enabled", "--enable-tts", "--disable-tts"),
        ("led_self_test", "--enable-led-self-test", "--disable-led-self-test"),
        ("display_enabled", "--enable-display", "--disable-display"),
        ("display_self_test", "--enable-display-self-test", "--disable-display-self-test"),
    )
    for field, enabled_flag, disabled_flag in toggles:
        value = getattr(config, field)
        if value is True:
            command.append(enabled_flag)
        elif value is False:
            command.append(disabled_flag)
    return command


def preview_device_config(config: DeviceWifiConfig, helper_path: Path) -> dict[str, Any]:
    return {
        "ok": True,
        "preview": True,
        "ssid_set": True,
        "password_set": True,
        "bssid_set": bool(config.bssid),
        "command": build_device_config_command(config, helper_path),
        **config.public_options(),
        "message": "Preview only; no firmware configuration was changed.",
    }


class DeviceConfigExecutor:
    """Execute one already-authorized and validated device-config plan."""

    def __init__(
        self,
        *,
        helper_path: Path,
        working_directory: Path,
        runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.helper_path = Path(helper_path)
        self.working_directory = Path(working_directory)
        self.runner = runner

    def execute(self, config: DeviceWifiConfig) -> dict[str, Any]:
        if not self.helper_path.exists():
            raise RuntimeError(f"configure helper not found: {self.helper_path}")
        env = os.environ.copy()
        env["WEARABLLM_WIFI_SSID"] = config.ssid
        env["WEARABLLM_WIFI_PASSWORD"] = config.password
        if config.bssid:
            env["WEARABLLM_WIFI_BSSID"] = config.bssid
        else:
            env.pop("WEARABLLM_WIFI_BSSID", None)
        result = self.runner(
            build_device_config_command(config, self.helper_path),
            cwd=str(self.working_directory),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Device configuration helper failed"
                if (result.stderr or result.stdout).strip()
                else f"configure_firmware.py exited {result.returncode}"
            )
        return {
            "ok": True,
            "ssid": config.ssid,
            "bssid": config.bssid or None,
            "password_set": True,
            **config.public_options(),
            "message": (
                "Updated ignored firmware/sdkconfig. Rebuild and flash firmware "
                "for changes to take effect."
            ),
        }
