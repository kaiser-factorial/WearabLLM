#!/usr/bin/env python3
"""Focused tests for WearabLLM serial microphone diagnostics."""

from __future__ import annotations

import unittest

from analyze_serial_log import INTERACTION_RE, collect_latest_mic_lanes, last_match


def lane_set(base_peak: int) -> str:
    return "\n".join(
        f"ES7210 packed lane {lane}: peak={base_peak + lane} rms={100 + lane} appears_silent=no"
        for lane in range(4)
    )


class MicLaneParserTest(unittest.TestCase):
    def test_collects_complete_lane_set(self) -> None:
        lanes = collect_latest_mic_lanes(lane_set(1000))
        self.assertEqual([lane["lane"] for lane in lanes], ["0", "1", "2", "3"])
        self.assertEqual(lanes[3]["peak"], "1003")

    def test_uses_latest_complete_capture(self) -> None:
        text = lane_set(1000) + "\n" + lane_set(2000)
        lanes = collect_latest_mic_lanes(text)
        self.assertEqual(lanes[0]["peak"], "2000")
        self.assertEqual(lanes[3]["peak"], "2003")

    def test_ignores_incomplete_lane_set(self) -> None:
        text = lane_set(1000) + "\nES7210 packed lane 0: peak=9 rms=2 appears_silent=no"
        lanes = collect_latest_mic_lanes(text)
        self.assertEqual(lanes[0]["peak"], "1000")


class InteractionParserTest(unittest.TestCase):
    def test_uses_latest_interaction_result(self) -> None:
        text = "\n".join(
            [
                "interaction #1 complete result=error total_ms=15000 err=ESP_ERR_TIMEOUT capture_source=onboard-mic",
                "interaction #2 complete result=ok total_ms=2310 command=GS capture_source=onboard-mic",
            ]
        )
        result = last_match(INTERACTION_RE, text)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["id"], "2")
        self.assertEqual(result["result"], "ok")
        self.assertEqual(result["capture_source"], "onboard-mic")


if __name__ == "__main__":
    unittest.main()
