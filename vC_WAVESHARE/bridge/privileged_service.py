"""Audited executors for already-authorized privileged bridge mutations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from bridge_policy import BridgePolicy, PolicyGrant, PrivilegedOperation
from device_config import (
    DeviceConfigExecutor,
    normalize_device_wifi_input,
    preview_device_config,
)


@dataclass(slots=True)
class PrivilegedMutationService:
    config_updater: Callable[[dict[str, Any]], Any]
    api_key_replacer: Callable[[str], dict[str, Any]]
    device_executor_factory: Callable[[], DeviceConfigExecutor]
    audit: Callable[..., None]

    def _audit(
        self,
        operation: PrivilegedOperation,
        outcome: str,
        *,
        grant: PolicyGrant,
        error_code: str | None = None,
    ) -> None:
        self.audit(
            operation.value,
            outcome,
            device_id=grant.principal_id,
            error_code=error_code,
        )

    def update_agent_config(
        self,
        grant: PolicyGrant,
        patch: dict[str, Any],
    ) -> Any:
        BridgePolicy.require_grant(grant, PrivilegedOperation.ADMIN_CONFIG_UPDATE)
        try:
            config = self.config_updater(patch)
        except ValueError:
            self._audit(
                PrivilegedOperation.ADMIN_CONFIG_UPDATE,
                "rejected",
                grant=grant,
                error_code="invalid_config",
            )
            raise
        except Exception:
            self._audit(
                PrivilegedOperation.ADMIN_CONFIG_UPDATE,
                "failed",
                grant=grant,
                error_code="persistence_failed",
            )
            raise
        self._audit(PrivilegedOperation.ADMIN_CONFIG_UPDATE, "accepted", grant=grant)
        return config

    def replace_api_key(self, grant: PolicyGrant, api_key: str) -> dict[str, Any]:
        BridgePolicy.require_grant(grant, PrivilegedOperation.API_KEY_UPDATE)
        try:
            result = self.api_key_replacer(api_key)
        except ValueError:
            self._audit(
                PrivilegedOperation.API_KEY_UPDATE,
                "rejected",
                grant=grant,
                error_code="invalid_api_key",
            )
            raise
        except Exception:
            self._audit(
                PrivilegedOperation.API_KEY_UPDATE,
                "failed",
                grant=grant,
                error_code="provider_validation_failed",
            )
            raise
        self._audit(PrivilegedOperation.API_KEY_UPDATE, "accepted", grant=grant)
        return result

    def configure_device(
        self,
        grant: PolicyGrant,
        payload: Mapping[str, Any],
        *,
        preview: bool,
    ) -> dict[str, Any]:
        BridgePolicy.require_grant(grant, PrivilegedOperation.DEVICE_CONFIG_UPDATE)
        try:
            config = normalize_device_wifi_input(payload)
            executor = self.device_executor_factory()
            result = (
                preview_device_config(config, executor.helper_path)
                if preview
                else executor.execute(config)
            )
        except ValueError:
            self._audit(
                PrivilegedOperation.DEVICE_CONFIG_UPDATE,
                "rejected",
                grant=grant,
                error_code="invalid_device_config",
            )
            raise
        except Exception:
            self._audit(
                PrivilegedOperation.DEVICE_CONFIG_UPDATE,
                "failed",
                grant=grant,
                error_code="device_config_failed",
            )
            raise
        self._audit(
            PrivilegedOperation.DEVICE_CONFIG_UPDATE,
            "previewed" if preview else "accepted",
            grant=grant,
        )
        return result
