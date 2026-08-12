"""Pure authorization and sensitive-operation decisions for the bridge."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Pattern

from action_queue import validate_device_id


class PrivilegedOperation(str, Enum):
    ADMIN_READ = "admin_read"
    ADMIN_CONFIG_UPDATE = "admin_config_update"
    API_KEY_UPDATE = "api_key_update"
    DEVICE_CONFIG_UPDATE = "device_config_update"
    MEMORY_MUTATION = "memory_mutation"
    ACTION_ACKNOWLEDGE = "action_acknowledge"
    TARGET_BODY_ACCESS = "target_body_access"


class MemoryMutationOutcome(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class AuthPrincipal:
    device_id: str
    authenticated: bool
    admin: bool


@dataclass(frozen=True, slots=True)
class PolicyGrant:
    operation: PrivilegedOperation
    principal_id: str
    target_device_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "principal_id", validate_device_id(self.principal_id))
        if self.target_device_id is not None:
            object.__setattr__(
                self,
                "target_device_id",
                validate_device_id(self.target_device_id),
            )


@dataclass(frozen=True, slots=True)
class MemoryMutationDecision:
    outcome: MemoryMutationOutcome
    categories: tuple[str, ...] = ()


class BridgePolicy:
    """Make allow/deny decisions without performing the approved mutation."""

    def __init__(
        self,
        *,
        admin_device_ids: Iterable[str] = (),
        shared_token_grants_admin: bool = True,
    ) -> None:
        self.admin_device_ids = frozenset(
            validate_device_id(device_id) for device_id in admin_device_ids
        )
        self.shared_token_grants_admin = bool(shared_token_grants_admin)

    def principal(self, device_id: str, *, authenticated: bool) -> AuthPrincipal:
        clean_device_id = validate_device_id(device_id)
        return AuthPrincipal(
            device_id=clean_device_id,
            authenticated=bool(authenticated),
            admin=bool(
                authenticated
                and (
                    self.shared_token_grants_admin
                    or clean_device_id in self.admin_device_ids
                )
            ),
        )

    @staticmethod
    def system_grant(operation: PrivilegedOperation) -> PolicyGrant:
        """Authorize an already-trusted in-process compatibility call."""
        return PolicyGrant(operation=operation, principal_id="local-bridge")

    @staticmethod
    def require_authenticated(principal: AuthPrincipal) -> None:
        if not principal.authenticated:
            raise PermissionError("Invalid or missing device token")

    def authorize_admin(
        self,
        principal: AuthPrincipal,
        operation: PrivilegedOperation,
    ) -> PolicyGrant:
        self.require_authenticated(principal)
        if not principal.admin:
            raise PermissionError("Administrative access is required")
        return PolicyGrant(operation=operation, principal_id=principal.device_id)

    def authorize_target(
        self,
        principal: AuthPrincipal,
        target_device_id: str,
        *,
        operation: PrivilegedOperation = PrivilegedOperation.TARGET_BODY_ACCESS,
    ) -> PolicyGrant:
        self.require_authenticated(principal)
        target = validate_device_id(target_device_id)
        if principal.device_id != target:
            raise PermissionError("Device ID does not match action target")
        return PolicyGrant(
            operation=operation,
            principal_id=principal.device_id,
            target_device_id=target,
        )

    @staticmethod
    def require_grant(grant: PolicyGrant, operation: PrivilegedOperation) -> None:
        """Validate a prior decision without re-deciding authorization."""
        if not isinstance(grant, PolicyGrant) or grant.operation is not operation:
            raise PermissionError("A matching policy grant is required")

    @staticmethod
    def require_tool_intent(
        tool_name: str,
        user_transcript: str,
        pattern: Pattern[str],
        denial_message: str,
    ) -> None:
        del tool_name
        if not pattern.search(user_transcript):
            raise PermissionError(denial_message)

    @staticmethod
    def eligible_tool_names(
        available_tool_names: Iterable[str],
        *,
        memory_available: bool,
        source_available: bool,
        memory_mutation_tool_names: Iterable[str] = (),
        force_memory_confirmation: bool = False,
        force_sensitive_stage: bool = False,
    ) -> frozenset[str]:
        """Return model tools eligible for one turn without executing any tool."""
        names = set(available_tool_names)
        requested_memory_tools = set(memory_mutation_tool_names)
        if force_memory_confirmation:
            names.intersection_update({"memory_confirm"})
        elif force_sensitive_stage:
            names.intersection_update({"memory_remember"})
        elif requested_memory_tools:
            names.intersection_update(requested_memory_tools)
        if not memory_available:
            names = {name for name in names if not name.startswith("memory_")}
        if not source_available:
            names = {name for name in names if not name.startswith("source_")}
        return frozenset(names)

    @staticmethod
    def web_search_eligible(*, configured: bool, requested_for_turn: bool) -> bool:
        return bool(configured and requested_for_turn)

    @staticmethod
    def require_allowlisted_target(
        tool_name: str,
        target_device_id: str,
        allowed_target_ids: Iterable[str],
    ) -> str:
        target = validate_device_id(target_device_id)
        if target not in frozenset(allowed_target_ids):
            raise ValueError(f"Unsupported or inactive target body: {target}")
        return target

    @staticmethod
    def decide_memory_mutation(
        blocked_categories: Iterable[str],
        confirmation_categories: Iterable[str],
    ) -> MemoryMutationDecision:
        blocked = tuple(sorted(set(blocked_categories)))
        if blocked:
            return MemoryMutationDecision(MemoryMutationOutcome.BLOCK, blocked)
        confirmation = tuple(sorted(set(confirmation_categories)))
        if confirmation:
            return MemoryMutationDecision(
                MemoryMutationOutcome.CONFIRM,
                confirmation,
            )
        return MemoryMutationDecision(MemoryMutationOutcome.ALLOW)


def operation_from_value(value: str | PrivilegedOperation) -> PrivilegedOperation:
    if isinstance(value, PrivilegedOperation):
        return value
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(value).strip().lower())
    return PrivilegedOperation(normalized)
