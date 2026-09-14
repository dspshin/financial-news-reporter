import unittest
from datetime import date, datetime

from morning_market_day import KST, KRX_CALENDAR_URL, closure_reason, validate_market_day


class MarketDayTests(unittest.TestCase):
    def test_fixed_and_observed_closures(self):
        for day in ("2026-09-19", "2026-09-24", "2026-10-05", "2026-05-01", "2026-12-31", "2028-12-29"):
            with self.subTest(day=day):
                self.assertIsNotNone(closure_reason(date.fromisoformat(day)))
        self.assertIsNone(closure_reason(date(2026, 9, 15)))

    def test_only_fresh_verified_open_days_pass(self):
        now = datetime(2026, 9, 15, 8, 30, tzinfo=KST)
        good = {"date": "2026-09-15", "status": "open", "checked_at": "2026-09-15T08:00:00+09:00",
                "sources": [KRX_CALENDAR_URL]}
        validate_market_day(good, now)
        for change in ({"status": "closed"}, {"status": "unknown"}, {"date": "2026-09-14"},
                       {"checked_at": "2026-09-14T08:00:00+09:00"},
                       {"checked_at": "2026-09-15T09:00:00+09:00"}, {"sources": []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_market_day(good | change, now)

    def test_open_claim_cannot_override_known_holiday(self):
        now = datetime(2026, 5, 1, 8, 30, tzinfo=KST)
        with self.assertRaisesRegex(ValueError, "closed"):
            validate_market_day({"date": "2026-05-01", "status": "open"}, now)


if __name__ == "__main__":
    unittest.main()
