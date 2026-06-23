import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from durable_memory import DurableMemoryStore, MemDatabaseStore, parse_memory_candidates


class ParseMemoryCandidatesTest(unittest.TestCase):
    def test_parses_json_array(self):
        self.assertEqual(
            parse_memory_candidates('["The user prefers tea to coffee."]'),
            ["The user prefers tea to coffee."],
        )

    def test_rejects_non_array_and_short_values(self):
        self.assertEqual(parse_memory_candidates('{"memory":"no"}'), [])
        self.assertEqual(parse_memory_candidates('["short"]'), [])

    def test_rejects_obvious_secrets_and_precise_contact_data(self):
        self.assertEqual(parse_memory_candidates('["The user password is swordfish."]'), [])
        self.assertEqual(parse_memory_candidates('["The user phone is 212-555-1234."]'), [])


class DurableMemoryStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "memory.json"
        self.store = DurableMemoryStore(self.path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_add_deduplicates_and_retrieves(self):
        self.assertTrue(self.store.add("The user prefers green tea in the morning."))
        self.assertFalse(self.store.add("The user prefers green tea in the morning."))
        self.assertEqual(
            self.store.retrieve("What tea does the user prefer?"),
            ["The user prefers green tea in the morning."],
        )
        self.assertEqual(len(self.store.list()), 1)

    def test_forget_and_clear(self):
        self.store.add("The user works on WearabLLM hardware.")
        self.store.add("The user prefers green tea.")
        self.assertEqual(self.store.forget("green tea"), 1)
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.store.clear(), 1)
        self.assertEqual(self.store.list(), [])


class MemDatabaseStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        (root / "bin").mkdir()
        (root / "bin" / "mem.js").write_text("// test stub\n")
        self.store = MemDatabaseStore(root)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("durable_memory.subprocess.run")
    def test_add_uses_private_keyed_mem_upsert(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout='{"success":true}', stderr="")
        self.assertTrue(self.store.add("The user prefers green tea."))
        command = run.call_args.args[0]
        self.assertIn("upsert", command)
        self.assertIn("--key", command)
        self.assertIn("wearabllm-home-assistant", command)
        self.assertIn("--local-only", command)
        self.assertEqual(command[-1], "--json")

    @patch("durable_memory.subprocess.run")
    def test_retrieve_returns_compact_mem_contents(self, run):
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout='{"results":[{"content":"The user prefers green tea."}]}',
            stderr="",
        )
        self.assertEqual(
            self.store.retrieve("What tea?"),
            ["The user prefers green tea."],
        )
        self.assertIn("--source", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
