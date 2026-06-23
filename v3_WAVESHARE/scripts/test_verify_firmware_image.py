#!/usr/bin/env python3
"""Tests for firmware image/config coherence checks."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from verify_firmware_image import verify


class FirmwareImageVerifierTest(unittest.TestCase):
    def make_firmware(self, bridge_url: str = "http://192.168.1.3:8765/v1/query") -> Path:
        root = Path(self.temp_dir.name) / "firmware"
        (root / "main").mkdir(parents=True)
        (root / "build" / "config").mkdir(parents=True)
        (root / "build" / "bootloader").mkdir(parents=True)
        (root / "build" / "partition_table").mkdir(parents=True)
        (root / "sdkconfig").write_text(
            "\n".join(
                [
                    'CONFIG_WEARABLLM_WIFI_SSID="test-network"',
                    'CONFIG_WEARABLLM_WIFI_PASSWORD="test-password"',
                    f'CONFIG_WEARABLLM_BRIDGE_URL="{bridge_url}"',
                    "# CONFIG_WEARABLLM_AUDIO_OUT_ENABLED is not set",
                    "# CONFIG_WEARABLLM_TTS_ENABLED is not set",
                    "# CONFIG_WEARABLLM_LED_SELF_TEST_ON_BOOT is not set",
                    "# CONFIG_WEARABLLM_DISPLAY_ENABLED is not set",
                    "# CONFIG_WEARABLLM_DISPLAY_SELF_TEST_ON_BOOT is not set",
                ]
            )
        )
        (root / "main" / "main.c").write_text("void app_main(void) {}\n")
        (root / "build" / "config" / "sdkconfig.h").write_text(
            f'#define CONFIG_WEARABLLM_BRIDGE_URL "{bridge_url}"\n'
        )
        (root / "build" / "bootloader" / "bootloader.bin").write_bytes(b"boot")
        (root / "build" / "partition_table" / "partition-table.bin").write_bytes(b"part")
        (root / "build" / "wearabllm_waveshare.bin").write_bytes(b"image:" + bridge_url.encode())
        return root

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_accepts_coherent_first_flash_image(self) -> None:
        result = verify(self.make_firmware(), require_first_flash_profile=True)
        self.assertTrue(result["ok"])

    def test_rejects_stale_embedded_bridge_url(self) -> None:
        root = self.make_firmware()
        (root / "build" / "wearabllm_waveshare.bin").write_bytes(
            b"image:http://192.168.86.31:8765/v1/query"
        )
        result = verify(root, require_first_flash_profile=False)
        self.assertFalse(result["ok"])
        failed = {check["name"] for check in result["checks"] if not check["ok"]}
        self.assertIn("bridge_url_embedded", failed)

    def test_rejects_enabled_optional_path_in_first_flash_profile(self) -> None:
        root = self.make_firmware()
        sdkconfig = root / "sdkconfig"
        sdkconfig.write_text(
            sdkconfig.read_text().replace(
                "# CONFIG_WEARABLLM_DISPLAY_ENABLED is not set",
                "CONFIG_WEARABLLM_DISPLAY_ENABLED=y",
            )
        )
        header = root / "build" / "config" / "sdkconfig.h"
        header.write_text(header.read_text() + "#define CONFIG_WEARABLLM_DISPLAY_ENABLED 1\n")
        binary = root / "build" / "wearabllm_waveshare.bin"
        binary.write_bytes(binary.read_bytes())
        result = verify(root, require_first_flash_profile=True)
        self.assertFalse(result["ok"])
        failed = {check["name"] for check in result["checks"] if not check["ok"]}
        self.assertIn("first_flash_profile", failed)


if __name__ == "__main__":
    unittest.main()
