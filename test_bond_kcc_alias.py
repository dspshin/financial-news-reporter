"""Real 10/27 KCC source values; synthetic histories and alternate issuances.

See fixtures/kcc_261027_provenance.json. No server history or delivery is used.
"""
import copy
from datetime import date, timedelta
from pathlib import Path
import unittest
from unittest.mock import Mock

import main


REFERENCE = date(2026, 10, 27)
FIXTURES = Path(__file__).parent / "fixtures"


def nh_event(**changes):
    text = (FIXTURES / "nh_261027_kcc_excerpt.txt").read_text()
    event, = main.parse_nh_syndication_text(text, REFERENCE, "http://nhisib.kr/nh261027.pdf")
    event.update(changes)
    return event


def dart_event(**changes):
    event = nh_event()
    for name in ("tranches", "amount_basis", "reported_total_eok", "managers", "validation_errors"):
        event.pop(name, None)
    clause = (FIXTURES / "kcc_261023_time_excerpt.txt").read_text()
    day, start, end = main.extract_dart_demand_schedule(clause, date(2026, 10, 23), date(2026, 11, 4))
    event.update(source="dart", issuer="케이씨씨", security_type="무보증사채",
                 demand_date=day, start_time=start, end_time=end, rate_band="개별민평 -30~+30bp",
                 report_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261023000404")
    event.update(changes)
    return event


def market(nh=None, dart=None, reference=REFERENCE):
    return {"enabled": True, "reference_date": reference,
            "nh": {"status": "ok", "source_date": reference,
                   "items": [nh_event()] if nh is None else nh},
            "dart": {"status": "ok", "items": [dart_event()] if dart is None else dart},
            "kofia": {"status": "empty", "items": [], "categories": {}}}


def record(event):
    return {"event": main.snapshot_bond_event(event), "missing_count": 0, "last_seen_at": "2026-10-26"}


class KccAliasTests(unittest.TestCase):
    def test_exact_aliases_and_subsidiaries(self):
        for name in ("KCC", "kcc", "케이씨씨", "주식회사 케이씨씨", "케이씨씨(주)", "㈜KCC"):
            with self.subTest(name=name):
                self.assertEqual(main.normalize_bond_issuer_key(name), "kcc")
        for name in ("KCC건설", "케이씨씨건설", "케이씨씨글라스", "KCC글라스"):
            self.assertNotEqual(main.normalize_bond_issuer_key(name), "kcc")

    def test_actual_conditions_merge_once_with_dart_time_and_nh_allocation(self):
        current = market()
        before = copy.deepcopy(current)
        event, = main.merge_bond_demand_events(current["dart"]["items"], current["nh"]["items"])
        self.assertEqual(main.bond_event_validation_errors(event), [])
        self.assertEqual(event["end_time"], "16:00")
        self.assertEqual(event["tranches"], [{"term": "2", "amount_eok": 800}, {"term": "3", "amount_eok": 1200}])
        self.assertEqual(event["amount_eok"], 2000)
        self.assertEqual(event["max_amount_eok"], 4000)
        self.assertIn("NH", event["managers"])
        self.assertEqual(current, before)
        digest = main.prepare_bond_schedule_digest(current, main.build_bond_history_state(), REFERENCE)
        self.assertTrue(digest["reliable"])
        self.assertEqual(len(digest["baseline_events"]), 1)
        self.assertEqual(len(digest["next_history"]["events"]), 1)
        section = main.build_bond_market_section(current, schedule_digest=digest)
        schedule = section.split("[ 금일 수요예측 일정 ]</b>")[1].split("\n\n", 1)[0]
        self.assertEqual(schedule.count("수요예측"), 1)
        self.assertIn("16:00", schedule)
        self.assertEqual(section.count("<b>■"), 1)
        self.assertIn("2년 800억원 / 3년 1,200억원", section)

    def test_minute_precision_survives_alias_merge(self):
        event, = main.merge_bond_demand_events([dart_event(start_time="09:15", end_time="16:30")], [nh_event()])
        self.assertEqual((event["start_time"], event["end_time"]), ("09:15", "16:30"))

    def test_rejected_nh_alias_cannot_reenter_through_dart(self):
        event, = main.merge_bond_demand_events([dart_event()], [],
            [{"issuer": "KCC", "errors": ["missing tranche amount"]}])
        self.assertTrue(main.bond_event_validation_errors(event))
        self.assertNotIn("억원", main.format_nh_mail_schedule_line(event))

    def test_conflicting_amounts_stay_pending_and_do_not_save(self):
        current = market(dart=[dart_event(amount_eok=2100)])
        digest = main.prepare_bond_schedule_digest(current, main.build_bond_history_state(), REFERENCE)
        self.assertFalse(digest["reliable"])
        saver = Mock()
        self.assertFalse(main.commit_bond_history_after_delivery(digest, True, True, saver=saver))
        saver.assert_not_called()
        self.assertNotIn("억원", main.build_bond_market_section(current, schedule_digest=digest))

    def test_known_band_and_manager_changes_are_not_hidden_as_enrichment(self):
        previous = main.snapshot_bond_event(nh_event(managers=["NH"]))
        current = main.snapshot_bond_event(nh_event(managers=["KB"], rate_band="개별민평 -20~+30bp"))
        changes = main.describe_bond_event_changes(previous, current)
        self.assertTrue(any("대표주관" in change for change in changes))
        self.assertTrue(any("밴드" in change for change in changes))

    def test_distinct_dates_are_not_merged(self):
        other = dart_event(demand_date=date(2026, 11, 10), payment_date=date(2026, 11, 18))
        events = main.merge_bond_demand_events([other], [nh_event()])
        self.assertEqual(len(events), 2)
        self.assertTrue(all(not main.bond_event_validation_errors(e) for e in events))

    def test_same_day_different_issuances_are_paired_by_known_metadata(self):
        for field, values in (("series", ("82", "83")),
                              ("payment_date", (date(2026, 11, 4), date(2026, 11, 5)))):
            with self.subTest(field=field):
                nh = [nh_event(**{field: v}) for v in values]
                dart = [dart_event(**{field: v}) for v in reversed(values)]
                current = market(nh=nh, dart=dart)
                events = main.merge_bond_demand_events(dart, nh)
                self.assertEqual(len(events), 2)
                self.assertEqual({e[field] for e in events}, set(values))
                self.assertTrue(all(not main.bond_event_validation_errors(e) for e in events))
                digest = main.prepare_bond_schedule_digest(current, main.build_bond_history_state(), REFERENCE)
                self.assertTrue(digest["reliable"])
                self.assertEqual(len(digest["baseline_events"]), 2)
                self.assertEqual(len(digest["next_history"]["events"]), 2)
                again = main.prepare_bond_schedule_digest(market(nh=list(reversed(nh)), dart=list(reversed(dart))),
                                                         digest["next_history"], REFERENCE)
                self.assertEqual(again["new_events"], [])
                self.assertEqual(again["changed_events"], [])
                self.assertEqual(len(again["today_events"]), 2)

    def test_ambiguous_missing_series_does_not_pick_an_arbitrary_nh_issue(self):
        current = market(nh=[nh_event(series="82"), nh_event(series="83")])
        digest = main.prepare_bond_schedule_digest(current, main.build_bond_history_state(), REFERENCE)
        self.assertFalse(digest["reliable"])
        self.assertIsNone(digest["next_history"])
        self.assertNotIn("억원", main.build_bond_market_section(current, schedule_digest=digest))

    def test_hybrid_and_regular_issuances_stay_separate(self):
        events = main.merge_bond_demand_events([dart_event()], [nh_event(issuer="KCC(신종)")])
        self.assertEqual(len(events), 2)
        self.assertEqual(len({main.bond_event_identity_key(e) for e in events}), 2)


class KccHistoryTests(unittest.TestCase):
    def assert_one_today(self, history):
        before = copy.deepcopy(history)
        digest = main.prepare_bond_schedule_digest(market(), history, REFERENCE)
        self.assertEqual(history, before)
        self.assertTrue(digest["reliable"])
        self.assertEqual(digest["new_events"], [])
        self.assertEqual(digest["changed_events"], [])
        self.assertEqual(digest["missing_alerts"], [])
        self.assertEqual(len(digest["today_events"]), 1)
        self.assertEqual(list(digest["next_history"]["events"]), ["kcc|regular"])
        return digest

    def test_prior_nh_name_is_not_a_new_dart_issuer(self):
        self.assert_one_today(main.build_bond_history_state(events={"kcc|regular": record(nh_event())}, initialized=True))

    def test_real_amount_change_under_alias_is_one_change_not_a_new_issuer(self):
        history = main.build_bond_history_state(events={"kcc|regular": record(nh_event())}, initialized=True)
        revised = nh_event(amount_eok=2100, tranches=[{"term": "2", "amount_eok": 900}, {"term": "3", "amount_eok": 1200}])
        digest = main.prepare_bond_schedule_digest(market(nh=[revised], dart=[dart_event(amount_eok=2100)]), history, REFERENCE)
        self.assertTrue(digest["reliable"])
        self.assertEqual(digest["new_events"], [])
        self.assertEqual(len(digest["changed_events"]), 1)
        self.assertTrue(any("2,000억원 → 2,100억원" in change for change in digest["changed_events"][0]["changes"]))

    def test_legacy_korean_key_is_matched_without_rewriting_input_history(self):
        self.assert_one_today(main.build_bond_history_state(events={"케이씨씨|regular": record(dart_event())}, initialized=True))

    def test_prior_compatible_alias_duplicates_converge_to_one_record(self):
        history = main.build_bond_history_state(events={"kcc|regular": record(nh_event()),
                                                       "케이씨씨|regular": record(dart_event())}, initialized=True)
        digest = self.assert_one_today(history)
        section = main.build_bond_market_section(market(), schedule_digest=digest)
        self.assertEqual(section.count("<b>■"), 1)
        self.assertIn("[당일]", section)
        self.assertNotIn("[신규]", section)

    def test_incompatible_alias_history_is_preserved_without_change_alerts(self):
        history = main.build_bond_history_state(events={"kcc|regular": record(nh_event()),
                                                       "케이씨씨|regular": record(dart_event(amount_eok=3000))}, initialized=True)
        before = copy.deepcopy(history)
        digest = main.prepare_bond_schedule_digest(market(), history, REFERENCE)
        self.assertFalse(digest["reliable"])
        self.assertIsNone(digest["next_history"])
        self.assertEqual(history, before)
        saver = Mock()
        self.assertFalse(main.commit_bond_history_after_delivery(digest, True, True, saver=saver))
        saver.assert_not_called()

    def test_distinct_prior_issue_is_not_overwritten_by_reused_base_key(self):
        old = nh_event(demand_date=date(2026, 10, 30), payment_date=date(2026, 11, 10), series="81")
        history = main.build_bond_history_state(events={"kcc|regular": record(old),
                                                       "케이씨씨|regular": record(nh_event())}, initialized=True)
        digest = main.prepare_bond_schedule_digest(market(), history, REFERENCE)
        self.assertTrue(digest["reliable"])
        self.assertEqual(len(digest["next_history"]["events"]), 2)
        self.assertEqual(digest["next_history"]["events"]["kcc|regular"]["event"], main.snapshot_bond_event(old))

    def test_one_unidentified_prior_record_cannot_match_two_current_series(self):
        nh = [nh_event(series="82"), nh_event(series="83")]
        dart = [dart_event(series="82"), dart_event(series="83")]
        history = main.build_bond_history_state(events={"kcc|regular": record(nh_event())}, initialized=True)
        digest = main.prepare_bond_schedule_digest(market(nh=nh, dart=dart), history, REFERENCE)
        self.assertFalse(digest["reliable"])
        self.assertIsNone(digest["next_history"])

    def test_remaining_issue_keeps_its_key_after_peer_expires(self):
        other_date = REFERENCE + timedelta(days=2)
        nh = [nh_event(series="82"), nh_event(series="83", demand_date=other_date, payment_date=date(2026, 11, 12))]
        dart = [dart_event(series="82"), dart_event(series="83", demand_date=other_date, payment_date=date(2026, 11, 12))]
        first = main.prepare_bond_schedule_digest(market(nh=nh, dart=dart), main.build_bond_history_state(), REFERENCE)
        second = main.prepare_bond_schedule_digest(market(nh=nh[1:], dart=dart[1:], reference=REFERENCE+timedelta(days=1)),
                                                  first["next_history"], REFERENCE+timedelta(days=1))
        self.assertTrue(second["reliable"])
        self.assertEqual(second["new_events"], [])
        self.assertEqual(second["changed_events"], [])
        self.assertEqual(len(second["unchanged_events"]), 1)
        self.assertEqual(len(second["next_history"]["events"]), 1)


if __name__ == "__main__":
    unittest.main()
