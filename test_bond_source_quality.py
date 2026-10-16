"""Real NH PDF layout; synthetic transport, modified layouts and KOFIA XML.

See fixtures/nh_261012_provenance.json for the actual PDF hash. These fixtures
are not the original GP execution inputs, and no delivery command is invoked.
"""

import copy
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import main
from nh_bond_parser import validation_errors


REFERENCE = date(2026, 10, 12)
FIXTURES = Path(__file__).parent / "fixtures"
LAYOUT = (FIXTURES / "nh_261012_layout.txt").read_text(encoding="utf-8")


def market_data(nh_status="empty"):
    data = {"enabled": True, "reference_date": REFERENCE,
            "dart": {"status": "empty", "items": []},
            "nh": {"status": nh_status, "source_date": REFERENCE, "items": []},
            "kofia": {"status": "pending", "items": [], "categories": {}, "pending_categories": {}}}
    return data


class ActualNhLayoutTests(unittest.TestCase):
    def parse(self, text=LAYOUT):
        errors = []
        events = main.parse_nh_syndication_text(text, REFERENCE, "https://example.com/nh.pdf", errors)
        return {event["issuer"]: event for event in events}, errors

    def test_actual_multi_row_header_reads_all_fifteen_issuers(self):
        events, errors = self.parse()
        self.assertEqual(errors, [])
        self.assertEqual(len(events), 15)
        self.assertTrue(all(not validation_errors(event) for event in events.values()))

    def test_actual_lotte_description_row_keeps_second_tenor(self):
        events, _ = self.parse()
        event = events["롯데리츠(담보부)"]
        self.assertEqual(event["tranches"], [{"term": "1", "amount_eok": 500}, {"term": "2", "amount_eok": 750}])
        self.assertEqual(main.format_tranche_amounts(event), "1년 500억원 / 2년 750억원")
        self.assertTrue(event["no_increase"])
        self.assertEqual(events["GS엔텍"]["term"], "2/3년")

    def test_missing_units_header_is_quarantined_instead_of_compact_fallback(self):
        damaged = "\n".join(line for line in LAYOUT.splitlines() if "(Y)" not in line)
        events, errors = self.parse(damaged)
        self.assertEqual(events, {})
        self.assertTrue(errors)

    def test_changed_column_labels_are_not_silently_read_in_the_old_order(self):
        lines = LAYOUT.splitlines()
        lines[2] = lines[2].replace("예측", "TEMP", 1).replace("발행", "예측", 1).replace("TEMP", "발행", 1)
        events, errors = self.parse("\n".join(lines))
        self.assertEqual(events, {})
        self.assertTrue(errors)

    def test_page_title_and_repeated_header_keep_description_continuation(self):
        lines = LAYOUT.splitlines()
        first = next(line for line in lines if "롯데리츠(담보부)" in line)
        following = next(line for line in lines if "롯데 프리미엄아울렛" in line)
        repeated = "\n".join(lines[:5] + [first, "\f"] + lines[:5] + [following])
        events, errors = self.parse(repeated)
        self.assertEqual(errors, [])
        self.assertEqual(events["롯데리츠(담보부)"]["amount_eok"], 1250)

    def test_actual_hyundai_predicted_and_issued_amounts_are_separate(self):
        events, _ = self.parse()
        event = events["현대트랜시스"]
        self.assertEqual(event["amount_eok"], 1500)
        self.assertEqual(event["issued_amount_eok"], 2000)
        self.assertEqual(event["issued_tranches"], [{"term": "2", "amount_eok": 800}, {"term": "3", "amount_eok": 1200}])
        self.assertEqual(main.snapshot_bond_event(event)["amount_eok"], 1500)
        self.assertNotIn("800억원", main.format_tranche_amounts(event))

    def test_actual_ls_and_hybrid_managers_remain_supported(self):
        events, _ = self.parse()
        self.assertEqual(events["LS증권"]["tranches"], [{"term": "1.5", "amount_eok": 400}, {"term": "2", "amount_eok": 300}])
        self.assertEqual(events["LS증권"]["amount_eok"], 700)
        self.assertEqual(events["JB금융지주(신종)"]["managers"], ["DB", "신한"])
        self.assertEqual(events["JB금융지주(신종)"]["term"], "99NC5")

    def test_issued_allocation_cannot_hide_invalid_sum_or_maximum(self):
        events, _ = self.parse()
        for changes in ({"issued_amount_eok": 1900}, {"max_amount_eok": 1700}, {"no_increase": True}):
            with self.subTest(changes=changes):
                event = copy.deepcopy(events["현대트랜시스"])
                event.update(changes)
                self.assertTrue(validation_errors(event))
        partial = copy.deepcopy(events["현대트랜시스"])
        partial["issued_tranches"] = [{"term": "2", "amount_eok": 3000}, {"term": "3", "amount_eok": None}]
        partial["issued_amount_eok"] = None
        self.assertTrue(validation_errors(partial))

    def test_actual_layout_fetch_includes_todays_issuance(self):
        response = Mock(status_code=200, content=b"%PDF-fixture")
        with patch.object(main, "extract_nh_syndication_pdf", return_value=(LAYOUT, None)):
            result = main.fetch_nh_syndication_schedule(REFERENCE, Mock(get=Mock(return_value=response)))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["validation_errors"], [])
        self.assertEqual([event["issuer"] for event in result["issuance_items"]], ["현대트랜시스"])
        data = market_data()
        data["nh"] = result
        section = main.build_bond_market_section(data)
        self.assertIn("회사채</b>: 현대트랜시스 (NH 예정)", section)

    def test_pdf_title_date_mismatch_is_not_treated_as_fresh(self):
        response = Mock(status_code=200, content=b"%PDF-fixture")
        wrong = LAYOUT.replace("2026-10-12 (월)", "2026-10-08 (목)", 1)
        with patch.dict("os.environ", {"NH_PDF_LOOKBACK_DAYS": "0"}), \
             patch.object(main, "extract_nh_syndication_pdf", return_value=(wrong, None)):
            result = main.fetch_nh_syndication_schedule(REFERENCE, Mock(get=Mock(return_value=response)))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["items"], [])
        self.assertIn("report date", result["errors"][0])

    def test_holiday_weekend_fallback_reaches_previous_business_day(self):
        responses = [Mock(status_code=404), Mock(status_code=200, content=b"%PDF-fixture")]
        old = LAYOUT.replace("2026-10-12 (월)", "2026-10-08 (목)", 1)
        requester = Mock(get=Mock(side_effect=responses))
        with patch.dict("os.environ", {"NH_PDF_LOOKBACK_DAYS": "1"}), \
             patch.object(main, "extract_nh_syndication_pdf", return_value=(old, None)):
            result = main.fetch_nh_syndication_schedule(REFERENCE, requester)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["source_date"], date(2026, 10, 8))
        self.assertIn("261008.pdf", requester.get.call_args.args[0])

    def test_year_boundary_and_next_leap_year_dates_remain_valid(self):
        for reference, demand, payment, expected_demand, expected_payment in (
            (date(2027, 1, 4), "12/29(화)", "1/7(목)", date(2026, 12, 29), date(2027, 1, 7)),
            (date(2023, 12, 29), "12/29(금)", "2/29(목)", date(2023, 12, 29), date(2024, 2, 29)),
        ):
            with self.subTest(reference=reference):
                synthetic = "\n".join(LAYOUT.splitlines()[:7]).replace("9/29(화)", demand).replace("10/7(수)", payment)
                errors = []
                events = main.parse_nh_syndication_text(synthetic, reference, "https://example.com/nh.pdf", errors)
                self.assertEqual(errors, [])
                self.assertEqual(events[0]["demand_date"], expected_demand)
                self.assertEqual(events[0]["payment_date"], expected_payment)

    def test_explicit_no_increase_conflicts_with_dart_maximum(self):
        events, _ = self.parse()
        nh = events["롯데리츠(담보부)"]
        dart = {**nh, "source": "dart", "max_amount_eok": 1500}
        merged, = main.merge_bond_demand_events([dart], [nh])
        self.assertTrue(main.bond_event_validation_errors(merged))
        self.assertEqual(main.format_tranche_amounts(merged), "조건 확인 중")


class BondScheduleCoverageTests(unittest.TestCase):
    def test_complete_empty_sources_describe_demand_schedule_scope(self):
        section = main.build_bond_market_section(market_data())
        self.assertIn("[ 금일 수요예측 일정 ]", section)
        self.assertIn("확인된 수요예측 일정 없음", section)
        self.assertNotIn("메일 본문에만 있는 입찰 일정은 포함되지 않습니다", section)
        self.assertNotIn("[ 금일 주요 일정 ]", section)
        self.assertNotIn("당일 원문 입력 필요", section)
        self.assertNotIn("\n- 없음\n", section)

    def test_incomplete_sources_never_assert_no_schedule(self):
        for status in ("error", "unavailable", "stale", "partial"):
            with self.subTest(status=status):
                section = main.build_bond_market_section(market_data(status))
                self.assertIn("일정 확인 불가", section)
                self.assertNotIn("확인된 수요예측 일정 없음", section)
                self.assertNotIn("\n- 없음\n", section)
        for source in ("nh", "dart"):
            with self.subTest(source=source):
                data = market_data()
                if source == "nh":
                    data[source]["source_date"] = date(2026, 10, 8)
                else:
                    data[source]["status"] = "error"
                self.assertIn("일정 확인 불가", main.build_bond_market_section(data))

    def test_empty_list_with_validation_failure_is_unconfirmed(self):
        data = market_data()
        data["nh"]["validation_errors"] = [{"issuer": "롯데리츠", "errors": ["incomplete continuation"]}]
        section = main.build_bond_market_section(data)
        self.assertIn("일정 확인 불가", section)
        self.assertIn("NH 예정표 일부 조건 확인 중", section)
        self.assertNotIn("이력 갱신", section)
        self.assertNotIn("확인된 수요예측 일정 없음", section)
        digest = main.prepare_bond_schedule_digest(data, main.build_bond_history_state(), REFERENCE)
        self.assertFalse(digest["reliable"])
        self.assertIsNone(digest["next_history"])

    def test_verified_nh_and_dart_still_show_today_without_mail_input(self):
        # Synthetic event date/time on an actual parsed condition bundle.
        event = main.parse_nh_syndication_text(LAYOUT, REFERENCE, "https://example.com/nh.pdf")[0]
        event = {**event, "demand_date": REFERENCE, "payment_date": date(2026, 10, 20)}
        data = market_data("ok")
        data["nh"]["items"] = [event]
        data["dart"] = {"status": "ok", "items": [{**event, "source": "dart", "end_time": "16:00"}]}
        digest = main.prepare_bond_schedule_digest(data, main.build_bond_history_state(), REFERENCE)
        self.assertTrue(digest["reliable"])
        self.assertIsNotNone(digest["next_history"])
        section = main.build_bond_market_section(data, schedule_digest=digest)
        schedule = section.split("[ 금일 수요예측 일정 ]</b>")[1].split("\n\n", 1)[0]
        self.assertIn(event["issuer"], schedule)
        self.assertIn("16:00", schedule)
        self.assertNotIn("일정 확인 불가", schedule)
        self.assertNotIn("확인된 수요예측 일정 없음", schedule)

    def test_ready_automatic_sources_finish_without_extra_file_wait(self):
        sleeper = Mock(side_effect=AssertionError("Ready sources must not wait"))
        result = main.fetch_bond_market_data(REFERENCE,
            now_provider=lambda: datetime(2026, 10, 12, 8, 10), sleeper=sleeper,
            dart_fetcher=Mock(return_value={"status": "empty", "items": []}),
            kofia_fetcher=Mock(return_value={"status": "ok", "items": [{"issuer": "아이엠뱅크"}]}),
            nh_fetcher=Mock(return_value={"status": "empty", "source_date": REFERENCE, "items": []}))
        self.assertEqual(result["poll_attempts"], 1)
        self.assertFalse(result["deadline_reached"])
        sleeper.assert_not_called()


class KofiaCoverageTests(unittest.TestCase):
    def test_im_bank_is_bank_and_nonbank_brand_is_not(self):
        self.assertEqual(main.classify_kofia_bond("아이엠뱅크47-10이(변)12A-12"), "은행채")
        self.assertEqual(main.normalize_kofia_issuer("아이엠뱅크47-10이(변)12A-12"), "아이엠뱅크")
        self.assertEqual(main.classify_kofia_bond("뱅크샐러드1"), "회사채")

    def test_zero_amount_im_bank_remains_visible_in_bank_category(self):
        # Synthetic XML envelope with the real 10/12 KOFIA name/zero amount.
        xml = "<r><BISComDspDatDTO><val1>아이엠뱅크47-10이(변)12A-12</val1><val3>20261012</val3><val6>0</val6></BISComDspDatDTO></r>"
        records, pending = main.parse_kofia_issuance_response(xml, include_pending=True)
        self.assertEqual(records, [])
        self.assertEqual(pending[0]["category"], "은행채")
        self.assertEqual(pending[0]["issuer"], "아이엠뱅크")

    def test_corporate_series_are_aggregated_under_one_issuer(self):
        self.assertEqual(main.normalize_kofia_issuer("현대트랜시스51-1"), "현대트랜시스")
        self.assertEqual(main.normalize_kofia_issuer("현대트랜시스51-2"), "현대트랜시스")

    def test_nh_supplement_deduplicates_names_and_preserves_exclusions(self):
        data = market_data("ok")
        # Synthetic issuer variants on the actual validated issuance conditions.
        event = next(event for event in main.parse_nh_syndication_text(LAYOUT, REFERENCE, "https://example.com/nh.pdf")
                     if event["issuer"] == "현대트랜시스")
        data["nh"]["issuance_items"] = [
            {**copy.deepcopy(event), "issuer": issuer}
            for issuer in ("현대트랜시스", "한국전력", "국민카드", "유플러스파이브지유동화")
        ]
        data["kofia"]["pending_categories"] = {"공사채": ["한국전력공사"], "여전채": ["케이비국민카드"]}
        before = copy.deepcopy(data)
        categories, supplements = main.bond_issuance_names(data["kofia"], data["nh"], REFERENCE)
        self.assertEqual(categories["공사채"].count("한국전력공사"), 1)
        self.assertEqual(categories["여전채"].count("케이비국민카드"), 1)
        self.assertIn("현대트랜시스", categories["회사채"])
        self.assertFalse(any("유동화" in name for name in categories["회사채"]))
        self.assertNotIn("한국전력공사", supplements)
        self.assertEqual(data, before)


if __name__ == "__main__":
    unittest.main()
