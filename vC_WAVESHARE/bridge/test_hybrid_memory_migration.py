from __future__ import annotations

import unittest
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase"
    / "migrations"
    / "20260810030000_add_hybrid_memory_search.sql"
)


class HybridMemoryMigrationTest(unittest.TestCase):
    def test_vector_schema_and_hybrid_rpc_are_private_and_principal_scoped(self):
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        self.assertIn("create extension if not exists vector", sql)
        self.assertIn("vector(512)", sql)
        self.assertIn("wearabllm_search_memory", sql)
        self.assertIn("principal_id = p_principal_id", sql)
        self.assertIn("status = 'active'", sql)
        self.assertIn("expires_at is null or", sql)
        self.assertIn("revoke all on function", sql)
        self.assertIn("from public, anon, authenticated", sql)
        self.assertIn("to service_role", sql)

    def test_search_rpc_return_shape_does_not_expose_embeddings(self):
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        returns_start = sql.index("returns table")
        body_start = sql.index("language sql", returns_start)
        return_shape = sql[returns_start:body_start]
        self.assertNotIn("embedding ", return_shape)
        self.assertIn("semantic_score", return_shape)
        self.assertIn("lexical_score", return_shape)
        self.assertIn("hybrid_score", return_shape)


if __name__ == "__main__":
    unittest.main()
