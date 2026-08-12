from __future__ import annotations

import json
import math
import unittest
from unittest.mock import MagicMock, patch

from household_memory import EMBEDDING_DIMENSIONS, SupabaseHouseholdMemoryStore


class SupabaseHouseholdMemoryStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SupabaseHouseholdMemoryStore(
            "https://example.supabase.co",
            "service-role-test",
            principal_id="home",
        )

    @staticmethod
    def embedding(seed: float = 0.01):
        return [seed] * EMBEDDING_DIMENSIONS

    @staticmethod
    def response(payload):
        result = MagicMock()
        result.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
        return result

    @staticmethod
    def record(**overrides):
        value = {
            "id": "11111111-1111-4111-8111-111111111111",
            "principal_id": "home",
            "subject": "Corina",
            "kind": "preference",
            "content": "Corina prefers jasmine tea.",
            "tags": ["tea"],
            "importance": 3,
            "confidence": 1.0,
            "status": "active",
        }
        value.update(overrides)
        return value

    @patch("household_memory.urllib.request.urlopen")
    def test_correction_uses_atomic_rpc_and_preserves_public_provenance(self, urlopen):
        replacement = self.record(
            id="22222222-2222-4222-8222-222222222222",
            content="Corina prefers oolong tea.",
            supersedes_id="11111111-1111-4111-8111-111111111111",
            source="wearabllm-explicit-tool",
        )
        urlopen.side_effect = [self.response([self.record()]), self.response([replacement])]

        corrected = self.store.correct(
            "11111111-1111-4111-8111-111111111111",
            content="Corina prefers oolong tea.",
            source_device_id="wearabllm-android",
        )

        request = urlopen.call_args_list[1].args[0]
        self.assertIn("/rest/v1/rpc/wearabllm_correct_memory", request.full_url)
        self.assertEqual(corrected["supersedes_id"], "11111111-1111-4111-8111-111111111111")
        self.assertEqual(corrected["source"], "wearabllm-explicit-tool")

    @patch("household_memory.urllib.request.urlopen")
    def test_correction_rejects_unchanged_content_before_rpc(self, urlopen):
        urlopen.return_value = self.response([self.record()])
        with self.assertRaisesRegex(ValueError, "must change"):
            self.store.correct(
                "11111111-1111-4111-8111-111111111111",
                content="Corina prefers jasmine tea.",
            )
        self.assertEqual(urlopen.call_count, 1)

    @patch("household_memory.urllib.request.urlopen")
    def test_hybrid_search_uses_principal_scoped_rpc_without_returning_vectors(self, urlopen):
        semantic_match = self.record(
            content="Corina avoids caffeinated drinks after sunset.",
            embedding=self.embedding(),
            semantic_score=0.92,
            lexical_score=0.0,
            hybrid_score=0.71,
        )
        urlopen.return_value = self.response([semantic_match])
        embed = MagicMock(return_value=self.embedding())
        store = SupabaseHouseholdMemoryStore(
            "https://example.supabase.co",
            "service-role-test",
            principal_id="home",
            embedding_provider=embed,
        )

        matches = store.search("What drinks should I skip in the evening?", limit=3)

        embed.assert_called_once_with("What drinks should I skip in the evening?")
        request = urlopen.call_args.args[0]
        self.assertIn("/rest/v1/rpc/wearabllm_search_memory", request.full_url)
        payload = json.loads(request.data)
        self.assertEqual(payload["p_principal_id"], "home")
        self.assertEqual(len(payload["p_query_embedding"]), EMBEDDING_DIMENSIONS)
        self.assertEqual(matches[0]["semantic_score"], 0.92)
        self.assertNotIn("embedding", matches[0])

    @patch("household_memory.urllib.request.urlopen")
    def test_remember_embeds_canonical_memory_and_never_exposes_vector(self, urlopen):
        created = self.record(embedding=self.embedding(), embedding_model="text-embedding-3-small")
        urlopen.side_effect = [self.response([]), self.response([created])]
        embed = MagicMock(return_value=self.embedding())
        store = SupabaseHouseholdMemoryStore(
            "https://example.supabase.co",
            "service-role-test",
            principal_id="home",
            embedding_provider=embed,
        )

        memory, was_created = store.remember(
            subject="Corina",
            kind="preference",
            content="Corina prefers jasmine tea.",
            tags=["tea"],
        )

        self.assertTrue(was_created)
        self.assertIn("Subject: Corina", embed.call_args.args[0])
        payload = json.loads(urlopen.call_args_list[1].args[0].data)
        self.assertEqual(len(payload["embedding"]), EMBEDDING_DIMENSIONS)
        self.assertEqual(payload["embedding_model"], "text-embedding-3-small")
        self.assertNotIn("embedding", memory)

    def test_embedding_validation_rejects_wrong_length_and_non_finite_values(self):
        for vector in ([0.1], [math.nan] * EMBEDDING_DIMENSIONS, [math.inf] * EMBEDDING_DIMENSIONS):
            with self.subTest(vector_length=len(vector)):
                store = SupabaseHouseholdMemoryStore(
                    "https://example.supabase.co",
                    "service-role-test",
                    principal_id="home",
                    embedding_provider=MagicMock(return_value=vector),
                )
                with self.assertRaises(ValueError):
                    store.search("tea")

    @patch("household_memory.urllib.request.urlopen")
    def test_correction_embeds_replacement_inside_atomic_rpc(self, urlopen):
        replacement = self.record(
            id="22222222-2222-4222-8222-222222222222",
            content="Corina prefers oolong tea.",
            supersedes_id="11111111-1111-4111-8111-111111111111",
        )
        urlopen.side_effect = [self.response([self.record()]), self.response([replacement])]
        store = SupabaseHouseholdMemoryStore(
            "https://example.supabase.co",
            "service-role-test",
            principal_id="home",
            embedding_provider=MagicMock(return_value=self.embedding()),
        )

        store.correct(
            "11111111-1111-4111-8111-111111111111",
            content="Corina prefers oolong tea.",
        )

        payload = json.loads(urlopen.call_args_list[1].args[0].data)
        self.assertEqual(len(payload["p_embedding"]), EMBEDDING_DIMENSIONS)
        self.assertEqual(payload["p_embedding_model"], "text-embedding-3-small")

    @patch("household_memory.urllib.request.urlopen")
    def test_backfill_only_patches_principal_scoped_missing_embeddings(self, urlopen):
        missing = self.record(embedding=None, embedding_model=None)
        ready = self.record(embedding=self.embedding(), embedding_model="text-embedding-3-small")
        urlopen.side_effect = [self.response([missing]), self.response([ready])]
        store = SupabaseHouseholdMemoryStore(
            "https://example.supabase.co",
            "service-role-test",
            principal_id="home",
            embedding_provider=MagicMock(return_value=self.embedding()),
        )

        self.assertEqual(store.backfill_missing_embeddings(limit=10), 1)
        get_request, patch_request = [entry.args[0] for entry in urlopen.call_args_list]
        self.assertIn("principal_id=eq.home", get_request.full_url)
        self.assertIn("embedding=is.null", get_request.full_url)
        self.assertIn("id=eq.11111111-1111-4111-8111-111111111111", patch_request.full_url)
        self.assertEqual(patch_request.method, "PATCH")


if __name__ == "__main__":
    unittest.main()
