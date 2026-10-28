"""Actual 10/28 NH attachment and DART values; synthetic failure/history cases.

See fixtures/nh_261028_provenance.json. No live delivery or server data is used.
"""
import copy
import json
from datetime import date, timedelta
from pathlib import Path
import unittest
from unittest.mock import Mock

import main
from nh_bond_parser import NHLayoutText, extract_pdf_cell_overrides


FIXTURES = Path(__file__).parent / "fixtures"
REFERENCE = date(2026, 10, 28)
PDF = (FIXTURES / "nh_261028.pdf").read_bytes()
LAYOUT = (FIXTURES / "nh_261028_layout.txt").read_text()


def parse(text):
    issues = []
    events = main.parse_nh_syndication_text(text, REFERENCE, "nh261028.pdf", issues)
    return {e["issuer"]: e for e in events}, issues


def actual_nh():
    text, _ = main.extract_nh_syndication_pdf(PDF)
    events, issues = parse(text)
    assert not issues, issues
    return events


def sk_dart(**changes):
    event = json.loads((FIXTURES / "sk_rentacar_261026_event.json").read_text())
    for field in ("receipt_date", "demand_date", "payment_date"):
        event[field] = date.fromisoformat(event[field])
    event.update(changes)
    return event


def market(nh, dart):
    return {"enabled": True, "reference_date": REFERENCE,
            "nh": {"status": "ok", "source_date": REFERENCE, "items": nh},
            "dart": {"status": "ok", "items": dart},
            "kofia": {"status": "empty", "items": [], "categories": {}}}


def record(event):
    return {"event": main.snapshot_bond_event(event), "missing_count": 0}


class ActualOct28PdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = actual_nh()

    def test_all_sixteen_issuers_recovered_with_cell_evidence(self):
        nh = main.fetch_nh_syndication_schedule(REFERENCE,
            Mock(get=Mock(return_value=Mock(status_code=200, content=PDF))))
        self.assertEqual(nh["status"], "ok")
        self.assertEqual(nh["validation_errors"], [])
        self.assertEqual(len(self.events), 16)
        self.assertIn("NH투자증권", [e["issuer"] for e in nh["issuance_items"]])

    def assert_kb_and_woori(self, events):
        kb, woori = events["KB금융지주(신종)"], events["우리투자증권"]
        self.assertEqual(kb["tranches"], [{"term": "99NC5", "amount_eok": 2700}])
        self.assertEqual((kb["amount_eok"], kb["max_amount_eok"]), (2700, 4000))
        self.assertEqual(kb["managers"], [])
        self.assertEqual(kb["rate_band"], "고정")
        self.assertEqual((kb["demand_date"], kb["payment_date"]), (date(2026, 11, 4), date(2026, 11, 12)))
        self.assertEqual(woori["rating"], "AA-(S)/A+(P)")
        self.assertEqual(woori["tranches"], [{"term": "2", "amount_eok": 400}, {"term": "3", "amount_eok": 600}])
        self.assertEqual((woori["amount_eok"], woori["max_amount_eok"]), (1000, 2000))
        self.assertEqual(woori["managers"], ["NH", "한투"])
        self.assertEqual(woori["rate_band"], "등급 -40~+40bp")
        self.assertEqual((woori["demand_date"], woori["payment_date"]), (date(2026, 11, 18), date(2026, 11, 26)))

    def test_hybrid_ends_before_composite_rated_next_issuer(self):
        self.assert_kb_and_woori(self.events)

    def test_plain_layout_also_separates_ratings_without_geometric_evidence(self):
        events, issues = parse(LAYOUT)
        self.assert_kb_and_woori(events)
        # The physically shared Posco amount must still be withheld in text.
        self.assertNotIn("포스코퓨처엠", events)
        self.assertTrue(issues)

    def test_shared_total_does_not_cross_into_adjacent_hybrid(self):
        posco = self.events["포스코퓨처엠"]
        self.assertEqual(posco["amount_eok"], 1000)
        self.assertEqual(posco["amount_basis"], "shared_total")
        self.assertEqual(posco["tranches"], [{"term": "2", "amount_eok": None}, {"term": "3", "amount_eok": None}])
        self.assertNotEqual(posco["amount_eok"], self.events["KB금융지주(신종)"]["amount_eok"])

    def test_invalid_new_issuer_rating_is_a_hard_boundary(self):
        # Synthetic future/unreadable grades must not donate metadata upstream.
        for rating in ("NR", "AA-(X)/A+(P)", "AA-(S)/UNKNOWN"):
            with self.subTest(rating=rating):
                events, issues = parse(LAYOUT.replace("AA-(S)/A+(P)", rating.ljust(11)))
                self.assertEqual(events["KB금융지주(신종)"]["tranches"], [{"term": "99NC5", "amount_eok": 2700}])
                self.assertEqual(events["KB금융지주(신종)"]["managers"], [])
                self.assertEqual(events["KB금융지주(신종)"]["rate_band"], "고정")
                self.assertNotIn("우리투자증권", events)
                self.assertTrue(any("unrecognized issuer row" in e["errors"] for e in issues))

    def test_supported_single_and_composite_rating_markers(self):
        for rating in ("AA-(S)", "A+(P)", "AA- / A+", "AA-(S)/A+(P)"):
            with self.subTest(rating=rating):
                events, _ = parse(LAYOUT.replace("AA-(S)/A+(P)", rating.ljust(11)))
                self.assertEqual(events["우리투자증권"]["rating"], rating.strip())
                self.assertEqual(events["우리투자증권"]["amount_eok"], 1000)
                self.assertEqual(events["KB금융지주(신종)"]["amount_eok"], 2700)

    def test_geometry_requires_all_rows_and_clipping_evidence(self):
        page = main.PdfReader(main.BytesIO(PDF)).pages[0]
        lines = LAYOUT.splitlines()
        del lines[next(i for i, line in enumerate(lines) if "우리투자증권" in line)]
        self.assertEqual(extract_pdf_cell_overrides(page, "\n".join(lines)), {})
        def extract(**callbacks):
            before = callbacks["visitor_operand_before"]
            def no_clip(op, args, cm, tm):
                if op not in {b"W", b"W*"}:
                    before(op, args, cm, tm)
            return page.extract_text(visitor_operand_before=no_clip, visitor_text=callbacks["visitor_text"])
        overrides = extract_pdf_cell_overrides(Mock(extract_text=extract), LAYOUT)
        events, issues = parse(NHLayoutText(LAYOUT, overrides))
        self.assert_kb_and_woori(events)
        self.assertNotIn("포스코퓨처엠", events)
        self.assertTrue(issues)

    def test_translated_coordinates_keep_issuer_boundaries(self):
        page = main.PdfReader(main.BytesIO(PDF)).pages[0]
        def extract(**callbacks):
            before = callbacks["visitor_operand_before"]
            def translated(op, args, cm, tm):
                cm = list(cm)
                cm[4] += 40
                cm[5] += 20
                before(op, args, cm, tm)
            return page.extract_text(visitor_operand_before=translated, visitor_text=callbacks["visitor_text"])
        events, issues = parse(NHLayoutText(LAYOUT, extract_pdf_cell_overrides(Mock(extract_text=extract), LAYOUT)))
        self.assertEqual(issues, [])
        self.assertEqual(len(events), 16)
        self.assert_kb_and_woori(events)

    def test_unknown_manager_does_not_inherit_nh_led_future_detail_eligibility(self):
        data = market([self.events[n] for n in ("KB금융지주(신종)", "우리투자증권")], [])
        self.assertEqual(len(main.merge_bond_demand_events([], data["nh"]["items"])), 2)
        digest = main.prepare_bond_schedule_digest(data, main.build_bond_history_state(), REFERENCE)
        self.assertTrue(digest["reliable"])
        self.assertEqual(list(digest["next_history"]["events"]), ["우리투자증권|regular"])
        section = main.build_bond_market_section(data, schedule_digest=digest)
        self.assertNotIn("KB금융지주(신종)", section)
        self.assertIn("우리투자증권", section)

    def test_distinct_issuers_render_separate_details_when_both_due_today(self):
        # Synthetic same-day dates exercise both details, including non-NH KB.
        data = market([dict(self.events[n], demand_date=REFERENCE)
                       for n in ("KB금융지주(신종)", "우리투자증권")], [])
        digest = main.prepare_bond_schedule_digest(data, main.build_bond_history_state(), REFERENCE)
        self.assertTrue(digest["reliable"])
        self.assertEqual(len(digest["next_history"]["events"]), 2)
        section = main.build_bond_market_section(data, schedule_digest=digest)
        kb = next(block for block in section.split("<b>■")[1:] if "KB금융지주(신종)" in block)
        self.assertIn("99NC5 2,700억원", kb)
        self.assertIn("고정", kb)
        self.assertNotIn("600억원", kb)
        self.assertNotIn("-40~+40", kb)
        self.assertNotIn("NH / 한투", kb)


class SkRentacarAliasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nh = actual_nh()["SK렌터카"]

    def test_exact_aliases_without_broad_sk_name_replacement(self):
        for name in ("SK렌터카", "에스케이렌터카", "주식회사 에스케이렌터카", "㈜SK렌터카"):
            self.assertEqual(main.normalize_bond_issuer_key(name), "sk렌터카")
        for name in ("SK렌터카서비스", "에스케이렌터카서비스", "SK네트웍스", "케이비렌터카"):
            self.assertNotEqual(main.normalize_bond_issuer_key(name), "sk렌터카")

    def test_leading_sk_prefix_with_identical_remaining_name(self):
        # Representative issuer names; these alternate cases are synthetic.
        for suffix in ("", "증권", "하이닉스", "이노베이션", "네트웍스", "에너지", "가스", "브로드밴드"):
            with self.subTest(suffix=suffix):
                expected = main.normalize_bond_issuer_key("SK" + suffix)
                for korean in ("에스케이" + suffix, "주식회사 에스케이 " + suffix,
                               "㈜에스케이" + suffix):
                    self.assertEqual(main.normalize_bond_issuer_key(korean), expected)

    def test_embedded_sk_and_different_suffixes_are_not_inferred_aliases(self):
        for left, right in (("뉴스SK", "뉴스에스케이"), ("SK증권", "에스케이증권서비스"),
                            ("SK하이닉스", "에스케이하이이엔지"), ("SKC", "에스케이씨")):
            with self.subTest(left=left, right=right):
                self.assertNotEqual(main.normalize_bond_issuer_key(left), main.normalize_bond_issuer_key(right))

    def test_other_sk_aliases_merge_and_match_compatible_history_once(self):
        for suffix in ("증권", "하이닉스", "이노베이션", "네트웍스"):
            with self.subTest(suffix=suffix):
                # Actual rental amounts with synthetic issuer names/histories.
                nh = dict(self.nh, issuer="SK" + suffix)
                dart = sk_dart(issuer="에스케이" + suffix)
                current = market([nh], [dart])
                prior = main.build_bond_history_state(events={
                    "sk" + suffix + "|regular": record(nh),
                    "에스케이" + suffix + "|regular": record(dart)}, initialized=True)
                before = copy.deepcopy(prior)
                digest = main.prepare_bond_schedule_digest(current, prior, REFERENCE)
                self.assertTrue(digest["reliable"])
                self.assertEqual(digest["new_events"], [])
                self.assertEqual(digest["changed_events"], [])
                self.assertEqual(digest["missing_alerts"], [])
                self.assertEqual(len(digest["today_events"]), 1)
                self.assertEqual(list(digest["next_history"]["events"]), ["sk" + suffix + "|regular"])
                self.assertEqual(prior, before)

    def test_general_sk_aliases_keep_distinct_issuances_and_subsidiaries(self):
        nh = dict(self.nh, issuer="SK증권", series="1")
        for fields in ({"issuer": "에스케이에너지"}, {"series": "2"},
                       {"demand_date": REFERENCE + timedelta(days=1), "payment_date": date(2026, 11, 6)},
                       {"payment_date": date(2026, 11, 6)}, {"security_type": "신종자본증권"}):
            with self.subTest(fields=fields):
                dart = sk_dart(issuer="에스케이증권", **fields) if "issuer" not in fields else sk_dart(**fields)
                self.assertEqual(len(main.merge_bond_demand_events([dart], [nh])), 2)

    def test_actual_conditions_render_one_schedule_detail_and_history(self):
        current = market([self.nh], [sk_dart()])
        before = copy.deepcopy(current)
        event, = main.merge_bond_demand_events(current["dart"]["items"], current["nh"]["items"])
        self.assertEqual(main.bond_event_validation_errors(event), [])
        self.assertEqual(event["tranches"], self.nh["tranches"])
        self.assertEqual((event["amount_eok"], event["max_amount_eok"]), (1000, 2000))
        self.assertEqual(event["managers"], ["KB"])
        self.assertEqual((event["start_time"], event["end_time"]), ("09:00", "16:00"))
        digest = main.prepare_bond_schedule_digest(current, main.build_bond_history_state(), REFERENCE)
        self.assertTrue(digest["reliable"])
        self.assertEqual(len(digest["baseline_events"]), 1)
        self.assertEqual(len(digest["next_history"]["events"]), 1)
        section = main.build_bond_market_section(current, schedule_digest=digest)
        schedule = section.split("[ 금일 수요예측 일정 ]</b>")[1].split("\n\n", 1)[0]
        self.assertEqual(schedule.count("수요예측"), 1)
        self.assertEqual(section.count("<b>■"), 1)
        self.assertIn("16:00", schedule)
        self.assertIn("2년 400억원 / 3년 600억원", section)
        self.assertEqual(current, before)

    def test_actual_dart_clause_and_synthetic_minutes_preserved(self):
        text = (FIXTURES / "sk_rentacar_261026_time_excerpt.txt").read_text()
        self.assertEqual(main.extract_dart_demand_schedule(text), (REFERENCE, "09:00", "16:00"))
        event, = main.merge_bond_demand_events([sk_dart(end_time="16:30")], [self.nh])
        self.assertEqual(event["end_time"], "16:30")

    def test_rejected_alias_and_conflicting_conditions_cannot_bypass_detail_guard(self):
        event, = main.merge_bond_demand_events([sk_dart()], [],
            [{"issuer": "SK렌터카", "errors": ["missing tranche amount"]}])
        self.assertTrue(main.bond_event_validation_errors(event))
        self.assertNotIn("억원", main.format_nh_mail_schedule_line(event))
        current = market([self.nh], [sk_dart(amount_eok=1200)])
        digest = main.prepare_bond_schedule_digest(current, main.build_bond_history_state(), REFERENCE)
        section = main.build_bond_market_section(current, schedule_digest=digest)
        self.assertIn("조건 확인 중", section)
        self.assertNotIn("억원", section)

    def test_distinct_date_series_payment_or_security_not_collapsed(self):
        for fields in ({"demand_date": REFERENCE + timedelta(days=1), "payment_date": date(2026, 11, 6)},
                       {"series": "2"}, {"payment_date": date(2026, 11, 6)},
                       {"issuer": "에스케이렌터카(신종)"}):
            with self.subTest(fields=fields):
                nh = dict(self.nh, series="1")
                events = main.merge_bond_demand_events([sk_dart(**fields)], [nh])
                self.assertEqual(len(events), 2)

    def test_legacy_alias_history_converges_without_false_change(self):
        for prior in ({"sk렌터카|regular": record(self.nh)},
                      {"에스케이렌터카|regular": record(sk_dart(max_amount_eok=2000))},
                      {"sk렌터카|regular": record(self.nh), "에스케이렌터카|regular": record(sk_dart())}):
            with self.subTest(keys=list(prior)):
                history = main.build_bond_history_state(events=prior, initialized=True)
                before = copy.deepcopy(history)
                digest = main.prepare_bond_schedule_digest(market([self.nh], [sk_dart()]), history, REFERENCE)
                self.assertTrue(digest["reliable"])
                self.assertEqual(digest["new_events"], [])
                self.assertEqual(digest["changed_events"], [])
                self.assertEqual(digest["missing_alerts"], [])
                self.assertEqual(len(digest["today_events"]), 1)
                self.assertEqual(list(digest["next_history"]["events"]), ["sk렌터카|regular"])
                self.assertEqual(history, before)

    def test_actual_dart_only_history_maximum_enrichment_is_one_issuer_change(self):
        prior = main.build_bond_history_state(events={"에스케이렌터카|regular": record(sk_dart())}, initialized=True)
        digest = main.prepare_bond_schedule_digest(market([self.nh], [sk_dart()]), prior, REFERENCE)
        self.assertTrue(digest["reliable"])
        self.assertEqual(digest["new_events"], [])
        self.assertEqual(digest["missing_alerts"], [])
        self.assertEqual(len(digest["changed_events"]), 1)
        self.assertEqual(digest["changed_events"][0]["changes"], ["최대 발행액 미정 → 2,000억원"])
        self.assertEqual(list(digest["next_history"]["events"]), ["sk렌터카|regular"])

    def test_conflicting_sources_and_history_cannot_write(self):
        history = main.build_bond_history_state(events={"sk렌터카|regular": record(self.nh),
            "에스케이렌터카|regular": record(sk_dart(amount_eok=1200))}, initialized=True)
        for current, prior in ((market([self.nh], [sk_dart(amount_eok=1200)]), main.build_bond_history_state()),
                               (market([self.nh], [sk_dart()]), history)):
            before = copy.deepcopy(prior)
            digest = main.prepare_bond_schedule_digest(current, prior, REFERENCE)
            self.assertFalse(digest["reliable"])
            self.assertIsNone(digest["next_history"])
            saver = Mock()
            self.assertFalse(main.commit_bond_history_after_delivery(digest, True, True, saver=saver))
            saver.assert_not_called()
            self.assertEqual(prior, before)

    def test_unreadable_issuer_row_keeps_snapshot_partial_and_blocks_history(self):
        broken = LAYOUT.replace("AA-(S)/A+(P)", "UNKNOWN    ")
        events, issues = parse(broken)
        current = market(list(events.values()), [sk_dart()])
        current["nh"].update(status="partial", validation_errors=issues)
        digest = main.prepare_bond_schedule_digest(current, main.build_bond_history_state(), REFERENCE)
        self.assertFalse(digest["reliable"])
        self.assertIsNone(digest["next_history"])
        self.assertEqual(digest.get("changed_events", []), [])
        saver = Mock()
        self.assertFalse(main.commit_bond_history_after_delivery(digest, True, True, saver=saver))
        saver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
