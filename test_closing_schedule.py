import unittest
from datetime import datetime, timezone

from closing_schedule import KST, closing_window


class ClosingScheduleTests(unittest.TestCase):
    def test_collection_starts_at_1600_but_finalization_waits_until_1620(self):
        for clock, phase in (("15:59:59", "waiting_for_start"),
                             ("16:00:00", "collecting"),
                             ("16:19:59", "collecting"),
                             ("16:20:00", "ready_to_finalize")):
            with self.subTest(clock=clock):
                window = closing_window(datetime.fromisoformat("2026-11-11T" + clock + "+09:00"))
                self.assertEqual(window["phase"], phase)
                self.assertEqual(window["collection_start"], "2026-11-11T16:00:00+09:00")
                self.assertEqual(window["cutoff"], "2026-11-11T16:20:00+09:00")
        self.assertEqual(closing_window(datetime(2026, 11, 11, 15, 59, 59, tzinfo=KST))["wait_seconds"], 1)

    def test_utc_clock_uses_korean_date_and_window(self):
        now = datetime(2026, 11, 11, 7, 0, tzinfo=timezone.utc)
        self.assertEqual(closing_window(now)["phase"], "collecting")
        self.assertEqual(closing_window(now)["wait_seconds"], 0)
        self.assertEqual(closing_window(now)["date"], "2026-11-11")
        with self.assertRaises(ValueError):
            closing_window(now.replace(tzinfo=None))
