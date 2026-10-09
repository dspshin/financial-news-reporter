"""Synthetic layout fixtures, NOT an archived NH261007 PDF or production text."""

import copy
import unittest
from datetime import date
from unittest.mock import Mock, patch

import main
from nh_bond_parser import validation_errors


POSITIONS = {"issuer": 0, "rating": 30, "term": 40, "amount": 52,
             "total": 66, "max": 80, "manager": 96, "band": 126,
             "demand": 152, "payment": 168}


def layout(**cells):
    line = [" "] * 185
    for name, value in cells.items():
        start = POSITIONS[name]
        line[start:start + len(str(value))] = str(value)
    return "".join(line).rstrip()


HEADER = layout(issuer="발행사", rating="등급", term="만기", amount="금액",
                total="합계", max="최대", manager="대표주관")


class NhBondParserTests(unittest.TestCase):
    reference = date(2026, 10, 7)
    url = "https://example.com/NH261007.pdf"

    def parse(self, *lines):
        issues = []
        events = main.parse_nh_syndication_text("\n".join(lines), self.reference, self.url, issues)
        return events, issues

    def first(self, issuer="롯데리츠", **cells):
        values = dict(issuer=issuer, rating="AA-", term="1", amount="500",
                      manager="NH/KB", band="개별 -30~+30", demand="10/8(목)", payment="10/16(금)")
        values.update(cells)
        return layout(**values)

    def data(self, events, **extra):
        nh = dict(status="ok", source_date=self.reference, items=events, pdf_url=self.url)
        nh.update(extra)
        return {"enabled": True, "reference_date": self.reference,
                "nh": nh, "dart": {"status": "empty", "items": []},
                "kofia": {"status": "empty", "items": [], "categories": {}}}

    def test_ls_shared_total_on_standalone_line_is_not_tenor(self):
        events, issues = self.parse(HEADER,
            self.first("LS증권", term="1.5", amount="", max="1,400", demand="10/14(수)", payment="10/21(수)"),
            layout(total="700"), layout(term="2"))
        self.assertEqual(issues, [])
        event, = events
        self.assertEqual(event["term"], "1.5/2년")
        self.assertEqual(event["amount_eok"], 700)
        self.assertEqual(event["max_amount_eok"], 1400)
        self.assertEqual(event["amount_basis"], "shared_total")
        self.assertEqual(event["demand_date"], date(2026, 10, 14))
        self.assertEqual(event["payment_date"], date(2026, 10, 21))
        self.assertEqual(main.format_tranche_amounts(event), "1.5/2년 700억원 (최대 1,400억원)")
        self.assertEqual([t["amount_eok"] for t in event["tranches"]], [None, None])

    def test_lotte_both_tranches_and_no_increase(self):
        events, issues = self.parse(HEADER, self.first(total="1,250", max="증액없음"), layout(term="2", amount="750"))
        self.assertEqual(issues, [])
        event, = events
        self.assertEqual(event["amount_eok"], 1250)
        self.assertEqual(event["term"], "1/2년")
        self.assertIsNone(event["max_amount_eok"])
        self.assertEqual(main.format_tranche_amounts(event), "1년 500억원 / 2년 750억원")
        section = main.build_bond_market_section(self.data(events))
        self.assertIn("1년 500억원 / 2년 750억원", section)
        self.assertIn("발행일: 10/16", section)

    def test_compact_inline_ls_and_short_indented_lotte(self):
        events, issues = self.parse(
            "LS증권  A+  1.5년/2년 700억(Max:1,400억) NH/KB  개별 -30~+30 10/14(수) 10/21(수)",
            "롯데리츠  AA-  1 500 (증액없음) NH  개별 -30~+30 10/8(목) 10/16(금)",
            "  2 750")
        self.assertEqual(issues, [])
        self.assertEqual([e["amount_eok"] for e in events], [700, 1250])
        self.assertEqual(events[1]["tranches"][1], {"term": "2", "amount_eok": 750})

    def test_wrapped_amount_and_repeated_page_header(self):
        events, issues = self.parse(HEADER, self.first(total="1,250"), "", "/", "\f", HEADER,
                                    layout(term="2"), layout(amount="750"))
        self.assertEqual(issues, [])
        self.assertEqual(events[0]["amount_eok"], 1250)
        self.assertEqual(len(events[0]["tranches"]), 2)

    def test_schedule_and_managers_can_wrap(self):
        events, issues = self.parse(HEADER, self.first(manager="", demand="", payment=""),
                                    layout(manager="NH/KB"), layout(demand="10/8(목)"), layout(payment="10/16(금)"))
        self.assertEqual(issues, [])
        self.assertEqual(events[0]["managers"], ["NH", "KB"])
        self.assertEqual(events[0]["payment_date"], date(2026, 10, 16))

    def test_multi_tranche_sum_matches_reported_total(self):
        events, issues = self.parse(HEADER, self.first(term="1.5", amount="300", total="1,500", max="3,000"),
                                    layout(term="2", amount="700"), layout(term="3", amount="500"))
        self.assertEqual(issues, [])
        self.assertEqual(events[0]["amount_eok"], 1500)
        self.assertEqual(events[0]["term"], "1.5/2/3년")

    def test_invalid_terms_are_rejected_without_losing_next_issuer(self):
        for term in ("0", "700", "101", "30NC40"):
            with self.subTest(term=term):
                events, issues = self.parse(HEADER, self.first("불량회사", term=term), self.first())
                self.assertEqual([e["issuer"] for e in events], ["롯데리츠"])
                self.assertIn("invalid or missing tenor", issues[0]["errors"])

    def test_normal_short_long_hybrid_and_perpetual_terms(self):
        for term in ("0.25", "1.5", "10", "20", "30", "50", "100", "30NC5", "60NC10", "영구"):
            with self.subTest(term=term):
                events, issues = self.parse(HEADER, self.first(term=term))
                self.assertEqual(issues, [])
                self.assertEqual(len(events), 1)

    def test_explicit_unknown_is_valid_but_blank_is_not(self):
        events, issues = self.parse(HEADER, self.first("교보생명보험(신종)", term="30NC5", amount="금액 미정", max="4,000", demand="미정"))
        self.assertEqual(issues, [])
        self.assertIsNone(events[0]["amount_eok"])
        self.assertEqual(events[0]["amount_basis"], "explicitly_unknown")
        events, issues = self.parse(HEADER, self.first(amount=""))
        self.assertEqual(events, [])
        self.assertIn("missing tranche amount without explicit total", issues[0]["errors"])

    def test_incomplete_followup_and_bare_amount_fail_closed(self):
        for continuation in ("                          700", "  2", "  2  750  100", "  2  0"):
            with self.subTest(continuation=continuation):
                events, issues = self.parse("롯데리츠  AA-  1 500 NH 10/8(목) 10/16(금)", continuation)
                self.assertEqual(events, [])
                self.assertTrue(issues)

    def test_orphan_amount_in_wrong_column_is_rejected(self):
        events, issues = self.parse(HEADER, self.first(), layout(amount="700"))
        self.assertEqual(events, [])
        self.assertIn("orphan amount cell", issues[0]["errors"])

    def test_partial_allocations_can_use_explicit_total_without_inventing_amounts(self):
        events, issues = self.parse(HEADER, self.first(total="1,250"), layout(term="2"))
        self.assertEqual(issues, [])
        self.assertEqual(main.format_tranche_amounts(events[0]), "1/2년 1,250억원")
        events, issues = self.parse(HEADER, self.first(total="400"), layout(term="2"))
        self.assertEqual(events, [])
        self.assertIn("tranche sum exceeds total", issues[0]["errors"])

    def test_truncated_lotte_and_bad_maximum_fail_validation(self):
        for cells, error in ((dict(total="1,250"), "reported total mismatch"),
                             (dict(max="400"), "maximum below total or invalid"),
                             (dict(amount="0"), "invalid tranche amount")):
            with self.subTest(cells=cells):
                events, issues = self.parse(HEADER, self.first(**cells))
                self.assertEqual(events, [])
                self.assertIn(error, issues[0]["errors"])

    def test_invalid_dates_and_missing_schedule_are_diagnostics(self):
        for cells in (dict(demand="10/32(수)"), dict(payment="10/1(목)"), dict(demand="", payment="")):
            with self.subTest(cells=cells):
                events, issues = self.parse(HEADER, self.first(**cells))
                self.assertEqual(events, [])
                self.assertTrue(issues)

    def test_conflicting_shared_total_is_not_overwritten(self):
        events, issues = self.parse(HEADER, self.first(total="500"), layout(total="700"))
        self.assertEqual(events, [])
        self.assertIn("conflicting total cells", issues[0]["errors"])

    def test_empty_extraction_is_not_a_successful_empty_schedule(self):
        events, issues = self.parse("")
        self.assertEqual(events, [])
        self.assertTrue(issues)
        self.assertEqual(self.parse("발행예정 없음"), ([], []))

    @patch("main.extract_nh_syndication_pdf")
    def test_fetch_keeps_good_events_but_marks_partial_snapshot(self, extract):
        extract.return_value = ("\n".join([HEADER, self.first("불량회사", term="700"), self.first()]), None)
        response = Mock(status_code=200, content=b"%PDF-fake")
        requester = Mock()
        requester.get.return_value = response
        nh = main.fetch_nh_syndication_schedule(self.reference, requester=requester)
        self.assertEqual(nh["status"], "partial")
        self.assertTrue(nh["validation_errors"])
        self.assertEqual([e["issuer"] for e in nh["items"]], ["롯데리츠"])
        data = self.data(nh["items"], **{k: v for k, v in nh.items() if k != "items"})
        digest = main.prepare_bond_schedule_digest(data, main.build_bond_history_state(), self.reference)
        self.assertFalse(digest["reliable"])
        self.assertIsNone(digest["next_history"])
        self.assertFalse(main.bond_sources_ready({"status": "ok", "items": [{}]}, nh, self.reference))
        section = main.build_bond_market_section(data, schedule_digest=digest)
        self.assertIn("검증 실패", section)
        self.assertIn("롯데리츠", section)
        self.assertNotIn("불량회사", section)
        self.assertNotIn("[변경]", section)

    def test_legacy_bad_ls_never_changes_or_saves_good_history(self):
        events, _ = self.parse(HEADER, self.first("LS증권", term="1.5/2", amount="700", max="1,400"))
        data = self.data(events)
        baseline = main.prepare_bond_schedule_digest(data, main.build_bond_history_state(), self.reference)
        history = baseline["next_history"]
        before = copy.deepcopy(history)
        bad = copy.deepcopy(events[0])
        bad.update(term="1.5/700/2년", amount_eok=None)
        bad["tranches"].insert(1, {"term": "700", "amount_eok": None})
        data = self.data([bad])
        digest = main.prepare_bond_schedule_digest(data, history, self.reference)
        self.assertFalse(digest["reliable"])
        self.assertIsNone(digest["next_history"])
        self.assertEqual(history, before)
        saver = Mock()
        self.assertFalse(main.commit_bond_history_after_delivery(digest, True, True, saver))
        saver.assert_not_called()
        section = main.build_bond_market_section(data, schedule_digest=digest)
        self.assertNotIn("700/", section)
        self.assertIn("LS증권", section)
        self.assertIn("조건 확인 중", section)
        self.assertNotIn("→", section)

    def test_dart_merge_cannot_hide_bad_nh_from_history_guard(self):
        events, _ = self.parse(HEADER, self.first())
        bad = copy.deepcopy(events[0])
        bad["tranches"].append({"term": "2", "amount_eok": None})
        bad["term"] = "1/2년"
        data = self.data([bad])
        dart = copy.deepcopy(events[0])
        dart["source"] = "dart"
        data["dart"] = {"status": "ok", "items": [dart]}
        merged = main.merge_bond_demand_events([dart], [bad])
        self.assertTrue(main.bond_event_validation_errors(merged[0]))
        self.assertEqual(main.format_tranche_amounts(merged[0]), "조건 확인 중")
        self.assertFalse(main.bond_schedule_snapshot_reliable(data, self.reference))

    def test_valid_amount_change_still_emits_condition_change(self):
        events, _ = self.parse(HEADER, self.first())
        baseline = main.prepare_bond_schedule_digest(self.data(events), main.build_bond_history_state(), self.reference)
        changed = copy.deepcopy(events[0])
        changed["tranches"][0]["amount_eok"] = changed["amount_eok"] = 600
        digest = main.prepare_bond_schedule_digest(self.data([changed]), baseline["next_history"], self.reference)
        self.assertTrue(digest["reliable"])
        self.assertEqual(len(digest["changed_events"]), 1)
        self.assertIn("발행액 500억원 → 600억원", digest["changed_events"][0]["changes"])

    def test_unknown_allocations_are_not_announced_as_condition_changes(self):
        events, _ = self.parse(HEADER,
            self.first("LS증권", term="1.5", amount="", total="700"), layout(term="2"))
        baseline = main.prepare_bond_schedule_digest(self.data(events), main.build_bond_history_state(), self.reference)
        history = baseline["next_history"]
        # Legacy parser incorrectly assigned a shared total to the first tenor.
        record = next(iter(history["events"].values()))
        record["event"]["tranches"][0]["amount_eok"] = 700
        record["fingerprint"] = main.bond_event_fingerprint(record["event"])
        digest = main.prepare_bond_schedule_digest(self.data(events), history, self.reference)
        self.assertTrue(digest["reliable"])
        self.assertEqual(digest["changed_events"], [])
        self.assertEqual(len(digest["unchanged_events"]), 1)

    def test_complete_allocation_change_is_still_reported(self):
        events, _ = self.parse(HEADER, self.first(total="1,250"), layout(term="2", amount="750"))
        baseline = main.prepare_bond_schedule_digest(self.data(events), main.build_bond_history_state(), self.reference)
        changed = copy.deepcopy(events[0])
        changed["tranches"][0]["amount_eok"] = 600
        changed["tranches"][1]["amount_eok"] = 650
        digest = main.prepare_bond_schedule_digest(self.data([changed]), baseline["next_history"], self.reference)
        self.assertEqual(len(digest["changed_events"]), 1)
        self.assertIn("만기별 금액", digest["changed_events"][0]["changes"][0])

    def test_geometry_is_independent_of_global_indentation(self):
        for shift in (0, 6, 30):
            with self.subTest(shift=shift):
                events, issues = self.parse(*[" " * shift + line for line in
                    (HEADER, self.first(total="1,250"), layout(term="2", amount="750"))])
                self.assertEqual(issues, [])
                self.assertEqual(events[0]["amount_eok"], 1250)

    def test_single_tenor_with_shared_total_displays_known_amount(self):
        events, issues = self.parse(HEADER, self.first(amount="", total="500"))
        self.assertEqual(issues, [])
        self.assertEqual(main.format_tranche_amounts(events[0]), "1년 500억원")

    def test_invalid_legacy_amount_types_are_rejected_without_crashing(self):
        events, _ = self.parse(HEADER, self.first())
        for value in ("500", float("nan"), float("inf"), -1, True):
            with self.subTest(value=value):
                bad = copy.deepcopy(events[0])
                bad["tranches"][0]["amount_eok"] = bad["amount_eok"] = value
                self.assertTrue(validation_errors(bad))
                self.assertFalse(main.bond_schedule_snapshot_reliable(self.data([bad]), self.reference))

    def test_unrecognized_issuer_row_does_not_disappear_from_snapshot_health(self):
        events, issues = self.parse(self.first(),
            "누락회사 AA- 2 750 NH 10/14(수) 10/21(수)")
        self.assertTrue(issues)
        self.assertEqual([e["issuer"] for e in events], ["롯데리츠"])

    def test_partial_snapshot_preserves_absence_counters(self):
        events, _ = self.parse(HEADER, self.first())
        history = main.prepare_bond_schedule_digest(self.data(events), main.build_bond_history_state(), self.reference)["next_history"]
        saved = copy.deepcopy(history)
        data = self.data([], status="partial", validation_errors=[{"issuer": "롯데리츠", "errors": ["missing tranche"]}])
        digest = main.prepare_bond_schedule_digest(data, history, self.reference)
        self.assertIsNone(digest["next_history"])
        self.assertEqual(saved, history)
        self.assertNotIn("missing_alerts", digest)

    @patch("main.PdfReader")
    def test_pdf_extraction_preserves_page_boundaries(self, reader):
        pages = [Mock(), Mock()]
        pages[0].extract_text.return_value = HEADER
        pages[1].extract_text.return_value = self.first()
        reader.return_value.pages = pages
        reader.return_value.metadata = None
        text, created = main.extract_nh_syndication_pdf(b"%PDF-fake")
        self.assertIn("\n\f\n", text)
        self.assertIsNone(created)
        pages[0].extract_text.assert_called_once_with(extraction_mode="layout", layout_mode_space_vertically=False)


class BondMergeSafetyTests(unittest.TestCase):
    """Synthetic input objects using the two amounts verified in NH's 10/8 XLSX.

    These are not the original PDF extraction or the GP execution's input cache.
    """
    reference = date(2026, 10, 8)

    def lotte(self):
        return {
            "source": "nh_pdf", "issuer": "롯데리츠", "rating": "AA-",
            "term": "1/2년", "amount_eok": 1250, "max_amount_eok": None,
            "amount_basis": "tranche_sum", "reported_total_eok": 1250,
            "tranches": [{"term": "1", "amount_eok": 500},
                         {"term": "2", "amount_eok": 750}],
            "demand_date": self.reference, "payment_date": date(2026, 10, 16),
            "managers": ["NH", "KB"], "report_url": "https://example.com/nh.pdf",
        }

    def dart(self, **changes):
        event = self.lotte()
        for field in ("tranches", "amount_basis", "reported_total_eok", "managers"):
            event.pop(field)
        event.update(source="dart", end_time="16:00", report_url="https://example.com/dart")
        event.update(changes)
        return event

    def data(self, dart, nh=None, **nh_changes):
        nh_result = {"status": "ok", "source_date": self.reference,
                     "items": [self.lotte()] if nh is None else nh}
        nh_result.update(nh_changes)
        return {"reference_date": self.reference, "enabled": True,
                "dart": {"status": "ok", "items": [dart]}, "nh": nh_result,
                "kofia": {"status": "empty", "categories": {}}}

    def assert_pending(self, data):
        baseline = main.prepare_bond_schedule_digest(
            self.data(self.dart()), main.build_bond_history_state(), self.reference)
        history = baseline["next_history"]
        before = copy.deepcopy(history)
        digest = main.prepare_bond_schedule_digest(data, history, self.reference)
        self.assertFalse(digest["reliable"])
        self.assertIsNone(digest["next_history"])
        self.assertEqual(history, before)
        saver = Mock()
        self.assertFalse(main.commit_bond_history_after_delivery(digest, True, True, saver))
        saver.assert_not_called()
        for supplied_digest in (None, digest):
            section = main.build_bond_market_section(data, schedule_digest=supplied_digest)
            self.assertIn("롯데리츠", section)
            self.assertIn("조건 확인 중", section)
            self.assertNotIn("억원", section)
            self.assertNotIn("→", section)
            self.assertNotIn("[변경]", section)

    def test_matching_aggregate_uses_both_nh_tranches_and_same_history(self):
        data = self.data(self.dart())
        before = copy.deepcopy(data)
        merged, = main.merge_bond_demand_events(data["dart"]["items"], data["nh"]["items"])
        self.assertEqual(main.bond_event_validation_errors(merged), [])
        self.assertEqual(main.format_tranche_amounts(merged), "1년 500억원 / 2년 750억원")
        self.assertIn("1/2년 1,250억원", main.format_nh_mail_schedule_line(merged))
        digest = main.prepare_bond_schedule_digest(data, main.build_bond_history_state(), self.reference)
        record = next(iter(digest["next_history"]["events"].values()))
        self.assertEqual(record["event"], main.snapshot_bond_event(merged))
        section = main.build_bond_market_section(data, schedule_digest=digest)
        self.assertIn("1년 500억원 / 2년 750억원", section)
        self.assertEqual(data, before)

    def test_partial_dart_allocation_cannot_erase_complete_nh_bundle(self):
        data = self.data(self.dart(tranches=[{"term": "1", "amount_eok": 500}]))
        event, = main.merge_bond_demand_events(data["dart"]["items"], data["nh"]["items"])
        self.assertEqual(main.bond_event_validation_errors(event), [])
        self.assertEqual(event["tranches"], self.lotte()["tranches"])
        self.assertIn("2년 750억원", main.format_tranche_amounts(event))

    def test_gp_oct8_aggregate_and_first_nh_tranche_cannot_mix(self):
        # Recreates the observed schedule/detail mismatch with synthetic inputs;
        # the actual PDF extraction and execution cache have not been recovered.
        nh = self.lotte()
        nh.update(term="1년", amount_eok=500, reported_total_eok=500,
                  tranches=[{"term": "1", "amount_eok": 500}])
        self.assert_pending(self.data(self.dart(), nh=[nh], status="partial",
            validation_errors=[{"issuer": "unknown", "errors": ["unrecognized row"]}]))

    def test_partial_dart_conditions_with_unknown_total_can_use_matching_nh(self):
        data = self.data(self.dart(term=None, amount_eok=None,
                                  tranches=[{"term": "1", "amount_eok": 500}]))
        event, = main.merge_bond_demand_events(data["dart"]["items"], data["nh"]["items"])
        self.assertEqual(main.bond_event_validation_errors(event), [])
        self.assertEqual(event["amount_eok"], 1250)

    def test_known_source_conflicts_never_alert_or_overwrite_history(self):
        for changes in (
            {"term": "1년", "amount_eok": 500},
            {"amount_eok": 1400}, {"term": "1/3년"},
            {"payment_date": date(2026, 10, 17)},
            {"tranches": [{"term": "1", "amount_eok": 600}]},
            {"reported_total_eok": 500}, {"max_amount_eok": 1000},
            {"series": "11"},
        ):
            with self.subTest(changes=changes):
                nh = self.lotte()
                nh["series"] = "10"
                self.assert_pending(self.data(self.dart(**changes), nh=[nh]))

    def test_dart_only_partial_detail_cannot_bypass_any_formatter(self):
        bad = self.dart(tranches=[{"term": "1", "amount_eok": 500}])
        self.assert_pending(self.data(bad, nh=[]))
        self.assertEqual(main.format_tranche_amounts(bad), "조건 확인 중")
        self.assertNotIn("억원", main.format_nh_mail_schedule_line(bad))
        detail = "\n".join(main.format_nh_mail_detail(bad, "변경", ["금액 500 → 1250"]))
        self.assertNotIn("변경", detail)
        self.assertNotIn("→", detail)
        # Even an injected reliable digest must use the same detail guard.
        section = main.build_bond_market_section(self.data(bad, nh=[]), schedule_digest={
            "reliable": True, "changed_events": [{"event": bad, "changes": ["500 → 1250"]}]})
        self.assertIn("조건 확인 중", section)
        self.assertNotIn("억원", section)
        self.assertNotIn("→", section)

    def test_rejected_nh_issuer_cannot_reappear_as_dart_details(self):
        self.assert_pending(self.data(self.dart(), nh=[], status="partial",
            validation_errors=[{"issuer": "롯데리츠(담보부)", "errors": ["missing second tenor"]}]))

    def test_demand_dates_conflict_for_same_payment_is_quarantined(self):
        self.assert_pending(self.data(self.dart(demand_date=date(2026, 10, 9))))

    def test_valid_dart_only_aggregate_does_not_invent_allocations(self):
        data = self.data(self.dart(), nh=[])
        digest = main.prepare_bond_schedule_digest(data, main.build_bond_history_state(), self.reference)
        self.assertTrue(digest["reliable"])
        section = main.build_bond_market_section(data, schedule_digest=digest)
        self.assertIn("1/2년 1,250억원", section)
        self.assertNotIn("1년 500억원", section)

    def test_ls_shared_total_is_valid_after_matching_dart_merge(self):
        nh = self.lotte()
        nh.update(issuer="LS증권", term="1.5/2년", amount_eok=700,
                  reported_total_eok=700, max_amount_eok=1400, amount_basis="shared_total",
                  tranches=[{"term": "1.5", "amount_eok": None}, {"term": "2", "amount_eok": None}])
        dart = {k: v for k, v in nh.items() if k not in {"tranches", "amount_basis", "reported_total_eok"}}
        dart["source"] = "dart"
        event, = main.merge_bond_demand_events([dart], [nh])
        self.assertEqual(main.bond_event_validation_errors(event), [])
        self.assertEqual(main.format_tranche_amounts(event), "1.5/2년 700억원 (최대 1,400억원)")

    def test_more_complete_dart_allocation_uses_same_resolved_history(self):
        nh = self.lotte()
        nh.update(amount_basis="shared_total", tranches=[
            {"term": "1", "amount_eok": None}, {"term": "2", "amount_eok": None}])
        dart = self.dart(tranches=self.lotte()["tranches"])
        data = self.data(dart, nh=[nh])
        digest = main.prepare_bond_schedule_digest(data, main.build_bond_history_state(), self.reference)
        self.assertTrue(digest["reliable"])
        record = next(iter(digest["next_history"]["events"].values()))
        self.assertEqual(record["event"]["tranches"], dart["tranches"])
        self.assertIn("2년 750억원", main.build_bond_market_section(data, schedule_digest=digest))

    def test_shared_nh_cannot_mask_invalid_complete_dart_sum(self):
        nh = self.lotte()
        nh.update(amount_basis="shared_total", tranches=[
            {"term": "1", "amount_eok": None}, {"term": "2", "amount_eok": None}])
        self.assert_pending(self.data(self.dart(tranches=[
            {"term": "1", "amount_eok": 500}, {"term": "2", "amount_eok": 500}]), nh=[nh]))

    def test_dart_allocation_can_take_missing_payment_metadata_from_nh(self):
        dart = self.dart(payment_date=None, tranches=self.lotte()["tranches"])
        event, = main.merge_bond_demand_events([dart], [self.lotte()])
        self.assertEqual(main.bond_event_validation_errors(event), [])
        self.assertEqual(event["payment_date"], date(2026, 10, 16))

    def test_explicitly_unknown_nh_does_not_erase_known_dart_aggregate(self):
        nh = self.lotte()
        nh.update(amount_eok=None, reported_total_eok=None, amount_basis="explicitly_unknown",
                  tranches=[{"term": "1", "amount_eok": None}, {"term": "2", "amount_eok": None}])
        event, = main.merge_bond_demand_events([self.dart()], [nh])
        self.assertEqual(main.bond_event_validation_errors(event), [])
        self.assertEqual(main.format_tranche_amounts(event), "1/2년 1,250억원")

    def test_invalid_known_dart_amount_cannot_be_hidden_by_shared_nh(self):
        nh = self.lotte()
        nh.update(amount_basis="shared_total", tranches=[
            {"term": "1", "amount_eok": None}, {"term": "2", "amount_eok": None}])
        self.assert_pending(self.data(self.dart(tranches=[{"term": "1", "amount_eok": -500}]), nh=[nh]))


    def test_distinct_issuances_for_same_issuer_remain_separate(self):
        dart = self.dart(demand_date=date(2026, 10, 20), payment_date=date(2026, 10, 28))
        events = main.merge_bond_demand_events([dart], [self.lotte()])
        self.assertEqual(len(events), 2)
        self.assertTrue(all(not main.bond_event_validation_errors(e) for e in events))

    def test_ambiguous_duplicate_nh_schedule_cannot_be_hidden_by_dart(self):
        self.assert_pending(self.data(self.dart(), nh=[self.lotte(), self.lotte()]))


if __name__ == "__main__":
    unittest.main()
