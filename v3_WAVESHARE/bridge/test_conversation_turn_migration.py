import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    REPO_ROOT
    / "supabase"
    / "migrations"
    / "20260812010000_expand_conversation_turn_content.sql"
)


class ConversationTurnMigrationTest(unittest.TestCase):
    def test_active_and_archive_turn_limits_are_expanded_together(self):
        sql = MIGRATION.read_text(encoding="utf-8").lower()

        self.assertIn("wearabllm_conversation_turns_content_check", sql)
        self.assertIn("wearabllm_conversation_archive_content_check", sql)
        self.assertEqual(sql.count("char_length(content) between 1 and 65536"), 2)


if __name__ == "__main__":
    unittest.main()
