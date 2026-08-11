from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy_hf_space.py"
SPEC = importlib.util.spec_from_file_location("deploy_hf_space", SCRIPT)
assert SPEC and SPEC.loader
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


class SourceManifestTest(unittest.TestCase):
    def test_private_source_bundle_is_allowlisted_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = deploy.build_source_bundle(Path(temporary))
            payload = json.loads(output.read_text(encoding="utf-8"))
            output_size = output.stat().st_size

        files = payload["files"]
        self.assertIn("v3_WAVESHARE/bridge/sphere_tools.py", files)
        self.assertIn("v3_WAVESHARE/bridge/source_code.py", files)
        self.assertIn("v3_WAVESHARE/app/src/App.tsx", files)
        self.assertIn("v3_WAVESHARE/firmware/main/main.c", files)
        self.assertIn("supabase/migrations/20260810030000_add_hybrid_memory_search.sql", files)
        lowered = "\n".join(files).lower()
        for forbidden in ("sdkconfig", "/build/", ".env", "node_modules", "credentials"):
            self.assertNotIn(forbidden, lowered)
        self.assertLess(output_size, deploy.MAX_SOURCE_BUNDLE_BYTES)

    def test_staged_space_contains_runtime_and_source_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = deploy.staged_space(root)
            relative = {path.relative_to(root).as_posix() for path in files}

        self.assertIn("source_bundle.json", relative)
        self.assertIn("bridge/source_code.py", relative)


if __name__ == "__main__":
    unittest.main()
