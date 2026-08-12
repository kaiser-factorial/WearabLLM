from __future__ import annotations

import json
import unittest
from http import HTTPStatus
from pathlib import Path

from v2_protocol import V2EnvelopeError, error_envelope, success_envelope, unwrap_envelope


FIXTURES = json.loads(
    (Path(__file__).parents[1] / "protocol" / "v2" / "fixtures.json").read_text(
        encoding="utf-8"
    )
)
SCHEMA = json.loads(
    (Path(__file__).parents[1] / "protocol" / "v2" / "envelope.schema.json").read_text(
        encoding="utf-8"
    )
)


class V2ProtocolTest(unittest.TestCase):
    def test_shared_success_fixture_round_trips(self) -> None:
        fixture = FIXTURES["success"]
        self.assertEqual(success_envelope(fixture["data"]), fixture)
        self.assertEqual(unwrap_envelope(fixture), fixture["data"])

    def test_shared_error_fixture_uses_typed_error(self) -> None:
        fixture = FIXTURES["error"]
        self.assertEqual(
            error_envelope(
                HTTPStatus.BAD_REQUEST,
                "Missing transcript",
                request_id="00000000000000000000000000000000",
            ),
            fixture,
        )
        with self.assertRaisesRegex(V2EnvelopeError, "Missing transcript"):
            unwrap_envelope(fixture)

    def test_success_wrapper_does_not_mutate_or_nest_legacy_ok(self) -> None:
        payload = {"ok": True, "devices": []}
        wrapped = success_envelope(payload)
        self.assertEqual(payload, {"ok": True, "devices": []})
        self.assertEqual(wrapped, {"ok": True, "data": {"devices": []}})

    def test_published_schema_requires_disjoint_success_and_error_shapes(self) -> None:
        success, error = SCHEMA["oneOf"]
        self.assertEqual(success["required"], ["ok", "data"])
        self.assertEqual(success["properties"]["ok"], {"const": True})
        self.assertEqual(error["required"], ["ok", "error"])
        self.assertEqual(error["properties"]["ok"], {"const": False})
        self.assertEqual(
            error["properties"]["error"]["required"],
            ["code", "message", "request_id"],
        )


if __name__ == "__main__":
    unittest.main()
