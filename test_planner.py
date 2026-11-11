"""Local tests for the carbon-aware planner. Run: python test_planner.py -v"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from urllib.error import URLError
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src", "planner_layer"))

import planner  # noqa: E402  (path is configured above for Lambda-layer layout)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"MOCK_MODE": "true"})
        env.start()
        self.addCleanup(env.stop)

    def test_synthetic_forecast_has_48_hours_and_daily_shape(self):
        forecast = planner.synthetic_forecast(NOW)
        self.assertTrue(forecast["mock"])
        self.assertEqual(len(forecast["points"]), 96)
        self.assertGreaterEqual(forecast["points"][0]["from"], planner.iso_z(NOW))

        values = [point["intensity"] for point in forecast["points"]]
        evening = values[10:18]  # roughly 17:00-21:00 from noon
        overnight = values[24:36]  # roughly 00:00-06:00
        self.assertGreater(max(evening), min(overnight) + 100)
        self.assertGreater(max(values), 300)
        self.assertLess(min(values), 150)

    def test_plan_selects_valid_window_before_deadline(self):
        deadline = NOW + timedelta(hours=30)
        result = planner.plan_job(4, deadline, now=NOW)

        start = planner.parse_utc(result["scheduled_start"])
        end = start + timedelta(hours=4)
        self.assertGreaterEqual(start, NOW)
        self.assertLessEqual(end, deadline)
        self.assertEqual(result["scheduled_end"], planner.iso_z(end))
        self.assertLess(result["avg_intensity"], result["immediate_intensity"])
        self.assertGreater(result["co2_saved"], 0)
        self.assertEqual(result["forecast_source"], "mock")

    def test_plan_finds_global_minimum_for_the_duration(self):
        duration = 4.0
        deadline = NOW + timedelta(hours=36)
        result = planner.plan_job(duration, deadline, now=NOW)

        points = planner.synthetic_forecast(NOW)["points"]
        needed = int(duration * 2)
        candidates = []
        for index in range(len(points) - needed + 1):
            window = points[index : index + needed]
            end = planner.parse_utc(window[0]["from"]) + timedelta(hours=duration)
            if end <= deadline and planner.parse_utc(window[-1]["to"]) >= end:
                average = sum(p["intensity"] for p in window) / needed
                candidates.append((average, window[0]["from"]))
        best_average, best_start = min(candidates)

        self.assertEqual(result["scheduled_start"], best_start)
        self.assertAlmostEqual(result["avg_intensity"], best_average, places=1)

    def test_deadline_too_soon_is_rejected(self):
        with self.assertRaises(planner.PlannerError):
            planner.plan_job(8, NOW + timedelta(hours=6), now=NOW)

    def test_invalid_durations_are_rejected(self):
        deadline = NOW + timedelta(hours=24)
        for duration in (0, 0.25, 24.5, -2, True, "two"):
            with self.subTest(duration=duration):
                with self.assertRaises(planner.PlannerError):
                    planner.plan_job(duration, deadline, now=NOW)

    def test_api_failure_falls_back_to_mock(self):
        with patch.dict(os.environ, {"MOCK_MODE": "false"}):
            with patch("planner.urlopen", side_effect=URLError("offline")):
                result = planner.plan_job(
                    2, NOW + timedelta(hours=28), now=NOW
                )
        self.assertEqual(result["forecast_source"], "mock")
        self.assertGreater(result["co2_saved"], 0)

        with patch.dict(os.environ, {"MOCK_MODE": "false"}):
            with patch("planner.urlopen", side_effect=URLError("offline")):
                forecast = planner.fetch_forecast(NOW)
        self.assertIn("offline", forecast["fallback_reason"])

    def test_immediate_window_cannot_beat_scheduled_window(self):
        result = planner.plan_job(3, NOW + timedelta(hours=26), now=NOW)
        self.assertLessEqual(result["co2_scheduled"], result["co2_now"])
        self.assertGreaterEqual(result["reduction_pct"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
