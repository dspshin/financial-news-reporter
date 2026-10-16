"""Actual NH PDF and GS clause, plus explicitly synthetic failure scenarios.

No briefing, delivery or operating-data command is executed. Provenance is in
fixtures/nh_261013_provenance.json; these are not archived server-run inputs.
"""

import copy
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import main
from nh_bond_parser import NHLayoutText, extract_pdf_cell_overrides


FIXTURES = Path(__file__).parent / "fixtures"
REFERENCE = date(2026, 10, 13)
PDF = (FIXTURES / "nh_261013.pdf").read_bytes()
LAYOUT = (FIXTURES / "nh_261013_layout.txt").read_text()
GS_CLAUSE = (FIXTURES / "gs_261008_demand_excerpt.txt").read_text()


def fetch_nh():
    return main.fetch_nh_syndication_schedule(
        REFERENCE, Mock(get=Mock(return_value=Mock(status_code=200, content=PDF))))


def data(nh=None):
    return {"enabled": True, "reference_date": REFERENCE,
            "nh": nh or fetch_nh(), "dart": {"status": "empty", "items": []},
            "kofia": {"status": "pending", "items": [], "categories": {},
                      "pending_categories": {"회사채": ["한국투자금융지주", "JB금융지주"]}}}


class DartMinuteTests(unittest.TestCase):
    def test_actual_gs_clause_preserves_thirty_minutes(self):
        parsed = main.extract_dart_demand_schedule(GS_CLAUSE, date(2026, 10, 8), date(2026, 10, 21))
        self.assertEqual(parsed, (REFERENCE, "09:00", "16:30"))

    def test_clock_formats_and_mixed_minutes(self):
        for clock, expected in (
            ("09시에서 16시까지", ("09:00", "16:00")),
            ("09시부터 16시30분까지", ("09:00", "16:30")),
            ("09시 15분부터 16시 45분까지", ("09:15", "16:45")),
            ("09:15~16:30", ("09:15", "16:30")),
            ("9:00 – 16:30", ("09:00", "16:30")),
            ("09시 30분부터 16:45", ("09:30", "16:45")),
            ("09:00~16:30 1,000억원 수요예측", ("09:00", "16:30")),
            ("09시부터 16시 1,000억원 수요예측", ("09:00", "16:00")),
        ):
            with self.subTest(clock=clock):
                text = "수요예측은 2026년 10월 13일 " + clock
                self.assertEqual(main.extract_dart_demand_schedule(text)[1:], expected)

    def test_invalid_or_reversed_clocks_are_not_rounded_into_valid_times(self):
        for clock in ("09:00~26:30", "09:00~16:70", "09시부터 16시99분까지",
                      "09:00~16:300", "16:30~09:00", "09시부터 16시 30까지"):
            with self.subTest(clock=clock):
                parsed = main.extract_dart_demand_schedule("수요예측은 2026년 10월 13일 " + clock)
                self.assertEqual(parsed, (REFERENCE, None, None))


class ActualPdfColumnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text, _ = main.extract_nh_syndication_pdf(PDF)
        cls.events = {e["issuer"]: e for e in main.parse_nh_syndication_text(cls.text, REFERENCE, "local.pdf")}

    def test_actual_pdf_reads_all_eighteen_issuers_and_today_issuance(self):
        nh = fetch_nh()
        self.assertEqual(nh["status"], "ok")
        self.assertEqual(nh["validation_errors"], [])
        self.assertEqual(len(self.events), 18)
        self.assertEqual({e["issuer"] for e in nh["issuance_items"]},
                         {"고려아연", "한국투자금융지주", "JB금융지주(신종)"})

    def test_underwriting_dash_does_not_enter_maximum_amount(self):
        event = self.events["고려아연"]
        self.assertEqual(event["max_amount_eok"], 3000)
        self.assertEqual(event["amount_eok"], 1500)
        self.assertEqual(event["issued_amount_eok"], 3000)
        self.assertEqual(self.events["한국투자금융지주"]["issued_amount_eok"], 5000)
        self.assertEqual(self.events["JB금융지주(신종)"]["issued_amount_eok"], 1000)

    def test_shared_cells_keep_total_without_inventing_allocations(self):
        for issuer, terms, total in (("롯데렌탈", ["1.5", "2.0", "3"], 1500),
                                     ("KCC", ["2", "3"], 2000)):
            with self.subTest(issuer=issuer):
                event = self.events[issuer]
                self.assertEqual(event["amount_basis"], "shared_total")
                self.assertEqual(event["amount_eok"], total)
                self.assertEqual(event["reported_total_eok"], total)
                self.assertEqual(event["tranches"], [{"term": term, "amount_eok": None} for term in terms])
                self.assertIn("NH", event["managers"])
                self.assertEqual(main.bond_event_validation_errors(event), [])

    def test_lotte_and_current_demand_conditions_are_preserved(self):
        self.assertEqual(self.events["롯데리츠(담보부)"]["tranches"],
                         [{"term": "1", "amount_eok": 500}, {"term": "2", "amount_eok": 750}])
        self.assertEqual(self.events["GS"]["tranches"],
                         [{"term": "2", "amount_eok": 500}, {"term": "3", "amount_eok": 500}])
        self.assertEqual(self.events["LS증권"]["amount_eok"], 700)

    def test_text_without_cell_evidence_cannot_assume_shared_total(self):
        issues = []
        events = main.parse_nh_syndication_text(LAYOUT, REFERENCE, "local.pdf", issues)
        self.assertNotIn("KCC", [e["issuer"] for e in events])
        self.assertNotIn("롯데렌탈", [e["issuer"] for e in events])
        self.assertTrue(issues)

    def test_missing_row_disables_geometry_alignment(self):
        page = main.PdfReader(main.BytesIO(PDF)).pages[0]
        lines = LAYOUT.splitlines()
        index = next(i for i, line in enumerate(lines) if "한국투자금융지주" in line)
        del lines[index + 1]
        self.assertEqual(extract_pdf_cell_overrides(page, "\n".join(lines)), {})

    def test_unproven_clipping_boxes_do_not_reclassify_missing_amounts(self):
        page = main.PdfReader(main.BytesIO(PDF)).pages[0]
        def extract(**callbacks):
            before = callbacks["visitor_operand_before"]
            def without_clip(op, operands, cm, tm):
                if op not in {b"W", b"W*"}:
                    before(op, operands, cm, tm)
            return page.extract_text(visitor_operand_before=without_clip,
                                     visitor_text=callbacks["visitor_text"])
        overrides = extract_pdf_cell_overrides(Mock(extract_text=extract), LAYOUT)
        issues = []
        events = main.parse_nh_syndication_text(NHLayoutText(LAYOUT, overrides), REFERENCE, "local.pdf", issues)
        self.assertNotIn("KCC", [e["issuer"] for e in events])
        self.assertNotIn("롯데렌탈", [e["issuer"] for e in events])
        self.assertTrue(issues)

    def test_translated_pdf_coordinates_preserve_conditions(self):
        page = main.PdfReader(main.BytesIO(PDF)).pages[0]
        def extract(**callbacks):
            before = callbacks["visitor_operand_before"]
            def translated(op, operands, cm, tm):
                matrix = list(cm)
                matrix[4] += 40
                matrix[5] += 20
                before(op, operands, matrix, tm)
            return page.extract_text(visitor_operand_before=translated,
                                     visitor_text=callbacks["visitor_text"])
        overrides = extract_pdf_cell_overrides(Mock(extract_text=extract), LAYOUT)
        issues = []
        events = main.parse_nh_syndication_text(NHLayoutText(LAYOUT, overrides), REFERENCE, "local.pdf", issues)
        self.assertEqual(issues, [])
        self.assertEqual(len(events), 18)
        self.assertEqual(next(e for e in events if e["issuer"] == "KCC")["amount_eok"], 2000)

    def test_geometry_evidence_stays_on_its_own_page(self):
        # Synthetic two-page document made from the same actual page.
        from pypdf import PdfWriter
        writer = PdfWriter()
        page = main.PdfReader(main.BytesIO(PDF)).pages[0]
        writer.add_page(page)
        writer.add_page(page)
        output = main.BytesIO()
        writer.write(output)
        text, _ = main.extract_nh_syndication_pdf(output.getvalue())
        issues = []
        events = main.parse_nh_syndication_text(text, REFERENCE, "multipage-fixture.pdf", issues)
        self.assertEqual(issues, [])
        kcc = [e for e in events if e["issuer"] == "KCC"]
        self.assertEqual(len(kcc), 2)
        self.assertTrue(all(e["amount_eok"] == 2000 and e["amount_basis"] == "shared_total" for e in kcc))

    def test_scaled_pdf_coordinates_preserve_column_boundaries(self):
        page = main.PdfReader(main.BytesIO(PDF)).pages[0]
        for scale in (0.8, 2):
            with self.subTest(scale=scale):
                def extract(**callbacks):
                    before = callbacks["visitor_operand_before"]
                    def scaled(op, operands, cm, tm):
                        before(op, operands, [v * scale for v in cm], tm)
                    return page.extract_text(visitor_operand_before=scaled,
                                             visitor_text=callbacks["visitor_text"])
                overrides = extract_pdf_cell_overrides(Mock(extract_text=extract), LAYOUT)
                issues = []
                events = main.parse_nh_syndication_text(NHLayoutText(LAYOUT, overrides), REFERENCE, "scaled.pdf", issues)
                self.assertEqual(issues, [])
                self.assertEqual(len(events), 18)
                self.assertEqual(next(e for e in events if e["issuer"] == "고려아연")["issued_amount_eok"], 3000)


class IssuedAmountDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nh = fetch_nh()

    def test_nh_issued_amounts_and_kofia_pending_are_distinct(self):
        current = data(copy.deepcopy(self.nh))
        before = copy.deepcopy(current)
        section = main.build_bond_market_section(current)
        self.assertIn("고려아연", section)
        self.assertIn("5년 3,000억원", section)
        self.assertIn("2년 2,600억원 / 3년 2,400억원 (합계 5,000억원)", section)
        self.assertIn("JB금융지주(신종)", section)
        self.assertIn("99NC5 1,000억원", section)
        self.assertIn("발행액 (NH 기준)", section)
        self.assertIn("금투협 발행액 미확인", section)
        self.assertEqual(current, before)
        event = next(e for e in current["nh"]["issuance_items"] if e["issuer"] == "한국투자금융지주")
        self.assertEqual(main.snapshot_bond_event(event)["amount_eok"], 2500)
        self.assertEqual(event["issued_amount_eok"], 5000)

    def test_stale_or_invalid_issued_conditions_are_not_displayed(self):
        for problem in ("stale", "wrong_date", "missing_issued", "sum", "maximum", "partial_issued", "condition_conflict"):
            with self.subTest(problem=problem):
                nh = copy.deepcopy(self.nh)
                event = next(e for e in nh["issuance_items"] if e["issuer"] == "한국투자금융지주")
                nh["issuance_items"] = [event]
                if problem == "stale": nh["status"] = "stale"
                elif problem == "wrong_date": nh["source_date"] = date(2026, 10, 12)
                elif problem == "missing_issued": event.pop("issued_tranches")
                elif problem == "sum": event["issued_amount_eok"] = 4000
                elif problem == "maximum": event["max_amount_eok"] = 4500
                elif problem == "partial_issued":
                    event["issued_tranches"][1]["amount_eok"] = None
                    event["issued_amount_eok"] = None
                elif problem == "condition_conflict": event["condition_errors"] = ["conflicting sources"]
                self.assertEqual(main.nh_issuance_amount_events(nh, REFERENCE), [])

    def test_duplicate_issuance_cannot_supply_an_arbitrary_amount(self):
        nh = copy.deepcopy(self.nh)
        event = nh["issuance_items"][0]
        nh["issuance_items"] = [event, copy.deepcopy(event)]
        self.assertEqual(main.nh_issuance_amount_events(nh, REFERENCE), [])

    def test_spc_and_excluded_issues_do_not_bypass_amount_filters(self):
        for issuer in ("유플러스파이브지유동화", "테스트사모", "테스트CB1"):
            with self.subTest(issuer=issuer):
                nh = copy.deepcopy(self.nh)
                event = nh["issuance_items"][0]
                event["issuer"] = issuer
                nh["issuance_items"] = [event]
                self.assertEqual(main.nh_issuance_amount_events(nh, REFERENCE), [])

    def test_bad_peer_rows_do_not_remove_a_valid_issued_bundle(self):
        nh = copy.deepcopy(self.nh)
        nh["status"] = "partial"
        nh["validation_errors"] = [{"issuer": "다른발행사", "errors": ["unreadable row"]}]
        self.assertEqual(len(main.nh_issuance_amount_events(nh, REFERENCE)), 3)
        nh["validation_errors"] = [{"issuer": "한국투자금융지주", "errors": ["conflicting row"]}]
        self.assertNotIn("한국투자금융지주", [e["issuer"] for e in main.nh_issuance_amount_events(nh, REFERENCE)])

    def test_good_actual_pdf_is_eligible_for_history_without_writing(self):
        current = data(copy.deepcopy(self.nh))
        digest = main.prepare_bond_schedule_digest(current, main.build_bond_history_state(), REFERENCE)
        self.assertTrue(digest["reliable"])
        saver = Mock(return_value=True)
        self.assertTrue(main.commit_bond_history_after_delivery(digest, True, True, saver=saver))
        saver.assert_called_once()
        self.assertFalse(main.commit_bond_history_after_delivery(digest, True, False, saver=saver))
        saver.assert_called_once()


if __name__ == "__main__":
    unittest.main()
