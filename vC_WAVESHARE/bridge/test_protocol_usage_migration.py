import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    REPO_ROOT
    / "supabase"
    / "migrations"
    / "20260812020000_add_protocol_usage_aggregates.sql"
)


class ProtocolUsageMigrationTest(unittest.TestCase):
    def test_aggregate_table_and_rpc_are_service_role_only(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8").lower()

        self.assertIn("create table if not exists public.wearabllm_protocol_usage_daily", sql)
        self.assertIn("enable row level security", sql)
        self.assertIn("revoke all on table", sql)
        self.assertIn("wearabllm_increment_protocol_usage", sql)
        self.assertIn("grant execute", sql)
        self.assertIn("to service_role", sql)
        self.assertIn("day < current_date - 89", sql)

    def test_schema_has_only_aggregate_dimensions(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        table_definition = sql.split("create table", 1)[1].split(");", 1)[0]

        for forbidden in (
            "device_id",
            "request_id",
            "raw_path",
            "query_parameter",
            "transcript",
            "credential",
            "payload_body",
        ):
            self.assertNotIn(forbidden, table_definition)


if __name__ == "__main__":
    unittest.main()
