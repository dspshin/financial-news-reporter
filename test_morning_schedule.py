import unittest
from datetime import datetime, timezone

from morning_schedule import KST, morning_window


class MorningScheduleTests(unittest.TestCase):
    def test_precollection_refresh_and_finalization_boundaries(self):
        for clock, phase in (("06:59:59", "waiting_for_start"),
                             ("07:00:00", "collecting"),
                             ("07:29:59", "collecting"),
                             ("07:30:00", "final_refresh"),
                             ("07:34:59", "final_refresh"),
                             ("07:35:00", "ready_to_finalize")):
            with self.subTest(clock=clock):
                window = morning_window(datetime.fromisoformat("2026-11-11T" + clock + "+09:00"))
                self.assertEqual(window["phase"], phase)
                self.assertEqual(window["collection_start"], "2026-11-11T07:00:00+09:00")
                self.assertEqual(window["final_refresh_start"], "2026-11-11T07:30:00+09:00")
                self.assertEqual(window["cutoff"], "2026-11-11T07:35:00+09:00")
                self.assertEqual(window["night_futures_not_before"], window["cutoff"])
        self.assertEqual(morning_window(datetime(2026, 11, 11, 6, 59, 59, tzinfo=KST))["wait_seconds"], 1)

    def test_utc_clock_uses_korean_date_and_window(self):
        now = datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc)
        self.assertEqual(morning_window(now)["phase"], "collecting")
        self.assertEqual(morning_window(now)["date"], "2026-11-11")
        self.assertEqual(morning_window(now)["wait_seconds"], 0)
        with self.assertRaises(ValueError):
            morning_window(now.replace(tzinfo=None))
