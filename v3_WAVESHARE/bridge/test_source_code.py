from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from source_code import SourceCodeStore


class SourceCodeStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.manifest = Path(self.temporary.name) / "source_bundle.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "version": 1,
                    "files": {
                        "README.md": "# Sphere\nPrivate bridge.\n",
                        "v3_WAVESHARE/bridge/sphere_tools.py": "line one\nline two\nline three\n",
                        "v3_WAVESHARE/docs/TOOLS.md": "# Tools\nRead only.\n",
                    },
                }
            ),
            encoding="utf-8",
        )
        self.store = SourceCodeStore(self.manifest)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_lists_only_manifest_paths_and_direct_children(self) -> None:
        root = self.store.list("", recursive=False, limit=20)
        self.assertEqual([entry["path"] for entry in root], ["README.md", "v3_WAVESHARE"])
        bridge = self.store.list("v3_WAVESHARE/bridge", recursive=True, limit=20)
        self.assertEqual(
            [entry["path"] for entry in bridge],
            ["v3_WAVESHARE/bridge/sphere_tools.py"],
        )
        self.assertEqual(bridge[0]["type"], "file")
        self.assertEqual(bridge[0]["lines"], 3)

    def test_reads_bounded_line_chunk_with_provenance(self) -> None:
        result = self.store.read(
            "v3_WAVESHARE/bridge/sphere_tools.py",
            start_line=2,
            line_count=1,
        )
        self.assertEqual(result["content"], "line two")
        self.assertEqual(result["start_line"], 2)
        self.assertEqual(result["end_line"], 2)
        self.assertEqual(result["total_lines"], 3)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["sha256"]), 64)

    def test_rejects_traversal_absolute_and_unlisted_paths(self) -> None:
        for path in ("../README.md", "/etc/passwd", "v3_WAVESHARE/bridge/missing.py"):
            with self.subTest(path=path), self.assertRaises((ValueError, LookupError)):
                self.store.read(path, start_line=1, line_count=20)

    def test_manifest_rejects_non_string_contents(self) -> None:
        self.manifest.write_text(
            json.dumps({"version": 1, "files": {"README.md": {"secret": True}}}),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            SourceCodeStore(self.manifest)


if __name__ == "__main__":
    unittest.main()
