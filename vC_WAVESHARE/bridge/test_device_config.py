from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge_policy import BridgePolicy, PrivilegedOperation
from device_config import (
    DeviceConfigExecutor,
    build_device_config_command,
    normalize_device_wifi_input,
    preview_device_config,
)
from privileged_service import PrivilegedMutationService


class DeviceConfigTest(unittest.TestCase):
    def test_executor_does_not_import_or_decide_policy(self) -> None:
        source = (Path(__file__).parent / "device_config.py").read_text(encoding="utf-8")
        self.assertNotIn("BridgePolicy", source)
        self.assertNotIn("PolicyGrant", source)

    def test_normalization_rejects_unsafe_and_out_of_range_values(self) -> None:
        invalid = (
            {"ssid": "network\nname", "password": "password"},
            {"ssid": "network", "password": "pass\x00word"},
            {"ssid": "network", "password": "password", "bssid": "not-a-bssid"},
            {"ssid": "network", "password": "password", "ptt_gpio": 49},
            {"ssid": "network", "password": "password", "audio_out_enabled": {}},
            {"ssid": "network", "password": "password", "tts_max_bytes": 4095},
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                normalize_device_wifi_input(payload)

    def test_preview_and_command_never_contain_wifi_secrets(self) -> None:
        ssid = "private-network-name"
        password = "private-network-password"
        config = normalize_device_wifi_input(
            {
                "ssid": ssid,
                "password": password,
                "bssid": "02:00:00:00:00:01",
                "ptt_gpio": 8,
                "display_enabled": True,
            }
        )

        preview = preview_device_config(config, Path("configure_firmware.py"))
        serialized = repr(preview)

        self.assertNotIn(ssid, serialized)
        self.assertNotIn(password, serialized)
        self.assertTrue(preview["ssid_set"])
        self.assertTrue(preview["password_set"])
        self.assertIn("--ptt-gpio", preview["command"])
        self.assertIn("--enable-display", preview["command"])
        self.assertNotIn(ssid, " ".join(build_device_config_command(config, Path("helper"))))

    def test_executor_passes_secrets_only_through_environment(self) -> None:
        runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
        with tempfile.NamedTemporaryFile() as helper:
            executor = DeviceConfigExecutor(
                helper_path=Path(helper.name),
                working_directory=Path(helper.name).parent,
                runner=runner,
            )
            config = normalize_device_wifi_input(
                {"ssid": "private-network", "password": "private-password"}
            )

            with patch.dict(
                os.environ,
                {"WEARABLLM_WIFI_BSSID": "02:00:00:00:00:99"},
            ):
                result = executor.execute(config)

        command = runner.call_args.args[0]
        environment = runner.call_args.kwargs["env"]
        self.assertNotIn("private-network", command)
        self.assertNotIn("private-password", command)
        self.assertEqual(environment["WEARABLLM_WIFI_SSID"], "private-network")
        self.assertEqual(environment["WEARABLLM_WIFI_PASSWORD"], "private-password")
        self.assertNotIn("WEARABLLM_WIFI_BSSID", environment)
        self.assertNotIn("password", {key.lower() for key in result})
        self.assertTrue(result["password_set"])

    def test_executor_failure_does_not_echo_helper_output(self) -> None:
        secret = "private-password-from-helper"
        runner = Mock(
            return_value=SimpleNamespace(returncode=1, stdout="", stderr=secret)
        )
        with tempfile.NamedTemporaryFile() as helper:
            executor = DeviceConfigExecutor(
                helper_path=Path(helper.name),
                working_directory=Path(helper.name).parent,
                runner=runner,
            )
            config = normalize_device_wifi_input(
                {"ssid": "private-network", "password": "private-password"}
            )
            with self.assertRaises(RuntimeError) as raised:
                executor.execute(config)
        self.assertNotIn(secret, str(raised.exception))


class PrivilegedMutationServiceTest(unittest.TestCase):
    def make_service(self) -> tuple[PrivilegedMutationService, Mock, Mock, Mock, list[dict[str, object]]]:
        config_updater = Mock(return_value={"source": "runtime"})
        api_key_replacer = Mock(return_value={"ok": True, "catalog": {}})
        executor = Mock()
        executor.helper_path = Path("configure_firmware.py")
        executor.execute.return_value = {"ok": True, "password_set": True}
        audit_events: list[dict[str, object]] = []

        def audit(operation: str, outcome: str, **fields: object) -> None:
            audit_events.append({"operation": operation, "outcome": outcome, **fields})

        service = PrivilegedMutationService(
            config_updater=config_updater,
            api_key_replacer=api_key_replacer,
            device_executor_factory=Mock(return_value=executor),
            audit=audit,
        )
        return service, config_updater, api_key_replacer, executor, audit_events

    def test_mismatched_grant_cannot_reach_any_executor(self) -> None:
        service, config_updater, api_key_replacer, executor, _events = self.make_service()
        wrong_grant = BridgePolicy.system_grant(PrivilegedOperation.ADMIN_READ)

        with self.assertRaises(PermissionError):
            service.update_agent_config(wrong_grant, {"tts_voice": "marin"})
        with self.assertRaises(PermissionError):
            service.replace_api_key(wrong_grant, "secret-api-key-value")
        with self.assertRaises(PermissionError):
            service.configure_device(
                wrong_grant,
                {"ssid": "network", "password": "password"},
                preview=False,
            )

        config_updater.assert_not_called()
        api_key_replacer.assert_not_called()
        executor.execute.assert_not_called()

    def test_audits_contain_metadata_but_not_mutation_values(self) -> None:
        service, config_updater, api_key_replacer, _executor, events = self.make_service()
        config_secret = "private delivery instructions"
        api_secret = "secret-api-key-value-never-log"

        service.update_agent_config(
            BridgePolicy.system_grant(PrivilegedOperation.ADMIN_CONFIG_UPDATE),
            {"tts_instructions": config_secret},
        )
        service.replace_api_key(
            BridgePolicy.system_grant(PrivilegedOperation.API_KEY_UPDATE),
            api_secret,
        )

        rendered = repr(events)
        self.assertNotIn(config_secret, rendered)
        self.assertNotIn(api_secret, rendered)
        self.assertEqual(config_updater.call_args.args[0]["tts_instructions"], config_secret)
        self.assertEqual(api_key_replacer.call_args.args[0], api_secret)


if __name__ == "__main__":
    unittest.main()
