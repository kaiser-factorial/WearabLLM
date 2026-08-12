from __future__ import annotations

import json
import unittest

from tool_activity import model_tool_context, public_tool_activity


class ToolActivityTest(unittest.TestCase):
    def test_memory_and_source_values_remain_private_to_model_context(self) -> None:
        memory_content = "Private household preference and address"
        source_content = "private source line contents"
        cases = (
            (
                "memory_remember",
                {
                    "ok": True,
                    "saved": True,
                    "created": True,
                    "memory": {"id": "m1", "content": memory_content},
                },
                {"content": memory_content},
                memory_content,
            ),
            (
                "source_read",
                {
                    "ok": True,
                    "file": {
                        "path": "bridge.py",
                        "start_line": 1,
                        "end_line": 2,
                        "content": source_content,
                    },
                },
                {"path": "bridge.py", "start_line": 1, "line_count": 2},
                source_content,
            ),
        )
        for name, result, arguments, private_value in cases:
            with self.subTest(tool=name):
                public = public_tool_activity(name, result, arguments).to_legacy_dict()
                private = model_tool_context(name, result, arguments)
                self.assertNotIn(private_value, json.dumps(public))
                self.assertIn(private_value, private.output)

    def test_backend_errors_are_not_exposed_in_public_activity(self) -> None:
        private_error = "Supabase row contained private household facts"
        public = public_tool_activity(
            "memory_correct",
            {"ok": False, "error": private_error},
            {},
        ).to_legacy_dict()

        self.assertNotIn(private_error, json.dumps(public))
        self.assertEqual(public["error"], "The private data backend rejected the request.")


if __name__ == "__main__":
    unittest.main()
