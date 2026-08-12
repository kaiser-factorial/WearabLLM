from __future__ import annotations

import re
import unittest
from pathlib import Path

from bridge_policy import (
    BridgePolicy,
    MemoryMutationOutcome,
    PrivilegedOperation,
)


class BridgePolicyTest(unittest.TestCase):
    def test_policy_module_has_no_mutation_or_runtime_integration(self) -> None:
        source = (Path(__file__).parent / "bridge_policy.py").read_text(encoding="utf-8")
        for forbidden in (
            "subprocess",
            "urllib",
            "action_queue.create",
            "memory_store",
            "agent_config.update",
            "Keychain",
        ):
            self.assertNotIn(forbidden, source)

    def test_shared_token_preserves_admin_compatibility_explicitly(self) -> None:
        policy = BridgePolicy(shared_token_grants_admin=True)
        principal = policy.principal("wearabllm-android", authenticated=True)

        grant = policy.authorize_admin(
            principal,
            PrivilegedOperation.ADMIN_CONFIG_UPDATE,
        )

        self.assertEqual(grant.principal_id, "wearabllm-android")
        self.assertEqual(grant.operation, PrivilegedOperation.ADMIN_CONFIG_UPDATE)

    def test_scoped_admin_policy_denies_non_admin_without_mutating_anything(self) -> None:
        policy = BridgePolicy(
            admin_device_ids={"web-console"},
            shared_token_grants_admin=False,
        )
        phone = policy.principal("wearabllm-android", authenticated=True)

        with self.assertRaises(PermissionError):
            policy.authorize_admin(phone, PrivilegedOperation.API_KEY_UPDATE)

    def test_target_grant_binds_authenticated_principal_to_same_body(self) -> None:
        policy = BridgePolicy()
        phone = policy.principal("wearabllm-android", authenticated=True)

        grant = policy.authorize_target(phone, "wearabllm-android")
        self.assertEqual(grant.target_device_id, "wearabllm-android")

        with self.assertRaises(PermissionError):
            policy.authorize_target(phone, "wearabllm-esp32")

    def test_unauthenticated_principal_cannot_receive_privileged_grant(self) -> None:
        policy = BridgePolicy()
        principal = policy.principal("web-console", authenticated=False)

        with self.assertRaises(PermissionError):
            policy.authorize_admin(principal, PrivilegedOperation.ADMIN_READ)

    def test_memory_policy_blocks_before_confirmation_and_allows_safe_values(self) -> None:
        policy = BridgePolicy()

        blocked = policy.decide_memory_mutation(["credentials"], ["email_address"])
        confirm = policy.decide_memory_mutation([], ["phone_number"])
        allowed = policy.decide_memory_mutation([], [])

        self.assertEqual(blocked.outcome, MemoryMutationOutcome.BLOCK)
        self.assertEqual(blocked.categories, ("credentials",))
        self.assertEqual(confirm.outcome, MemoryMutationOutcome.CONFIRM)
        self.assertEqual(allowed.outcome, MemoryMutationOutcome.ALLOW)

    def test_tool_intent_and_target_eligibility_are_pure_decisions(self) -> None:
        policy = BridgePolicy()
        pattern = re.compile(r"\bsend\b", re.IGNORECASE)

        policy.require_tool_intent("send_to_body", "Send it to the phone", pattern, "denied")
        target = policy.require_allowlisted_target(
            "send_to_body",
            "wearabllm-android",
            {"wearabllm-android"},
        )
        self.assertEqual(target, "wearabllm-android")

        with self.assertRaises(PermissionError):
            policy.require_tool_intent("send_to_body", "Maybe later", pattern, "denied")
        with self.assertRaises(ValueError):
            policy.require_allowlisted_target(
                "send_to_body",
                "attacker-body",
                {"wearabllm-android"},
            )

    def test_turn_tool_eligibility_is_capability_and_intent_scoped(self) -> None:
        policy = BridgePolicy()
        names = {
            "memory_search",
            "memory_remember",
            "source_read",
            "send_to_body",
        }

        normal = policy.eligible_tool_names(
            names,
            memory_available=False,
            source_available=False,
        )
        mutation = policy.eligible_tool_names(
            names,
            memory_available=True,
            source_available=True,
            memory_mutation_tool_names={"memory_remember"},
        )

        self.assertEqual(normal, frozenset({"send_to_body"}))
        self.assertEqual(mutation, frozenset({"memory_remember"}))
        self.assertTrue(
            policy.web_search_eligible(configured=True, requested_for_turn=True)
        )
        self.assertFalse(
            policy.web_search_eligible(configured=True, requested_for_turn=False)
        )


if __name__ == "__main__":
    unittest.main()
