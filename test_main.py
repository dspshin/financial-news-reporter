import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import requests

import main


class PefFilterTests(unittest.TestCase):
    def test_accepts_pef_industry_and_deal_headlines(self):
        content = "사모펀드 업계 제도 개선과 운용사 의견을 다룬 기사입니다. " * 12
        association = main.evaluate_pef_article(
            "PEF협의회, 사모펀드협회 전환 추진 - 연합인포맥스",
            "https://example.com/association",
            content,
        )
        deal = main.evaluate_pef_article(
            "KKR, 국내 소프트웨어 기업 경영권 인수 본입찰 - 더벨",
            "https://example.com/deal",
            content,
        )

        self.assertTrue(association["accepted"])
        self.assertTrue(deal["accepted"])

    def test_rejects_generic_it_and_public_share_sale(self):
        content = "IT 시스템 통합과 데이터센터 투자에 관한 일반 산업 기사입니다. " * 12
        generic_it = main.evaluate_pef_article(
            "삼성 로봇조직 통합에 IT서비스 업계 방긋 - 한국경제",
            "https://example.com/it",
            content,
        )
        public_sale = main.evaluate_pef_article(
            "신동빈, 롯데쇼핑 주식 매도해 개인 유동성 확보 - 매일경제",
            "https://example.com/sale",
            content,
        )
        shareholder_sale = main.evaluate_pef_article(
            "암콤리, 주주들 주당 135펜스에 지분 10% 매각 예정 - Investing.com 한국어",
            "https://example.com/shareholder-sale",
            content,
        )
        low_signal_opinion = main.evaluate_pef_article(
            "사모펀드는 에이전트 AI의 부상에 따라갈 수 있을까? - AI넷",
            "https://example.com/opinion",
            content,
        )
        retail_fund = main.evaluate_pef_article(
            "삼성증권, 사모펀드 재간접 공모펀드 단독 판매 - 경인방송 뉴스",
            "https://example.com/retail-fund",
            content,
        )

        self.assertFalse(generic_it["accepted"])
        self.assertFalse(public_sale["accepted"])
        self.assertFalse(shareholder_sale["accepted"])
        self.assertFalse(low_signal_opinion["accepted"])
        self.assertFalse(retail_fund["accepted"])

    def test_weather_metaphor_does_not_hide_a_real_pef_story(self):
        result = main.evaluate_pef_article(
            "사모펀드發 유통 M&A, 규제 한파에 얼어붙는다 - 매일일보",
            "https://example.com/pef-regulation",
            "사모펀드의 유통기업 인수와 규제 영향을 분석한 기사입니다. " * 12,
        )
        self.assertTrue(result["accepted"])


class EventDedupeTests(unittest.TestCase):
    def test_matches_different_headlines_for_same_event(self):
        first = "신동빈 롯데 회장, 롯데쇼핑 지분 1.15% 매각…유동성 확보 - 연합뉴스"
        second = "롯데 신동빈, 롯데쇼핑 지분 매각해 423억 확보…개인 유동성 목적 - 한국경제"
        self.assertTrue(main.is_same_news_event(first, second))

    def test_does_not_merge_different_deals(self):
        first = "KKR, A사 경영권 인수 본입찰 - 더벨"
        second = "MBK, B사 경영권 인수 본입찰 - 더벨"
        self.assertFalse(main.is_same_news_event(first, second))

    def test_matches_pef_association_transition_variants(self):
        first = 'PEF협의회, 협회로 전환 추진…규제 움직임엔 "적극 소통" - 연합뉴스'
        second = "PEF협의회, 다음달 협회 전환 투표…협회장 인선도 새로 - 이투데이"
        self.assertTrue(main.is_same_news_event(first, second))


class HistoryTransactionTests(unittest.TestCase):
    def test_staging_does_not_mark_article_collected(self):
        history = main.build_news_history_state([], "/tmp/news-history-test.json")
        pending = []
        entry = SimpleNamespace(title="A사 경영권 매각 - 더벨", link="https://example.com/a")

        main.stage_article_for_history(pending, entry, "pef", collected_date=date(2026, 7, 23))
        self.assertEqual(history["articles"], [])
        self.assertEqual(len(pending), 1)

        committed = main.commit_pending_articles(history, pending)
        self.assertEqual(committed, 1)
        self.assertEqual(len(history["articles"]), 1)

    @patch.dict("os.environ", {"TELEGRAM_PEF_CHANNEL_ID": "@pef"}, clear=False)
    @patch("main.setup_logging")
    @patch("main.check_holidays", return_value=(False, False, None, None))
    @patch("main.wait_until_pef_start")
    @patch("main.save_news_history")
    @patch("main.send_telegram_message", side_effect=[True, True, False])
    @patch("main.generate_briefing", side_effect=["<b>general</b>", "<b>pef</b>"])
    @patch("main.fetch_bond_market_data", return_value={"enabled": False})
    @patch("main.fetch_firm_mention_news")
    @patch("main.fetch_news")
    @patch("main.fetch_market_data", return_value={})
    @patch("main.load_news_history")
    @patch("main.sys.argv", ["main.py"])
    def test_main_commits_only_fully_delivered_groups(
        self,
        mock_load_history,
        _mock_market,
        mock_fetch_news,
        mock_fetch_firm,
        _mock_bond_market,
        _mock_generate,
        _mock_send,
        mock_save,
        _mock_wait,
        _mock_holidays,
        _mock_logging,
    ):
        history = main.build_news_history_state([], "/tmp/news-history-test.json")
        mock_load_history.return_value = history
        success_status = main.new_fetch_status("test")
        success_status.update({"queries_attempted": 1, "queries_succeeded": 1})
        general_pending = [{
            "link": "https://example.com/general",
            "title": "General news",
            "title_key": "general news",
            "target": "general",
            "collected_at": "2026-07-23",
        }]
        pef_pending = [{
            "link": "https://example.com/pef",
            "title": "PEF deal",
            "title_key": "pef deal",
            "target": "pef",
            "collected_at": "2026-07-23",
        }]
        mock_fetch_news.side_effect = [
            ("general context", [("General news", "https://example.com/general")], set(), general_pending, success_status),
            ("pef context", [("PEF deal", "https://example.com/pef")], set(), pef_pending, success_status),
        ]
        mock_fetch_firm.return_value = ("", [], set(), [], success_status)

        main.main()

        self.assertEqual([item["target"] for item in history["articles"]], ["general"])
        mock_save.assert_called_once_with(history)


class PefScheduleTests(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {"PEF_WAIT_ENABLED": "true", "PEF_START_TIME": "08:10"},
        clear=False,
    )
    def test_waits_until_configured_pef_start_time(self):
        sleeper = Mock()

        waited = main.wait_until_pef_start(
            date(2026, 7, 24),
            now=datetime(2026, 7, 24, 7, 40),
            sleeper=sleeper,
        )

        self.assertEqual(waited, 30 * 60)
        sleeper.assert_called_once_with(30 * 60)

    def test_test_mode_skips_wait(self):
        sleeper = Mock()

        waited = main.wait_until_pef_start(
            date(2026, 7, 24),
            test_mode=True,
            now=datetime(2026, 7, 24, 7, 40),
            sleeper=sleeper,
        )

        self.assertEqual(waited, 0)
        sleeper.assert_not_called()


class FetchStatusTests(unittest.TestCase):
    @patch("main.requests.get", side_effect=requests.RequestException("network down"))
    def test_all_rss_failures_are_reported_as_outage(self, _mock_get):
        context, links, _seen, pending, status = main.fetch_news(target="general")

        self.assertEqual(context, "")
        self.assertEqual(links, [])
        self.assertEqual(pending, [])
        self.assertTrue(main.is_fetch_outage(status))
        briefing = main.generate_briefing(
            {}, "", target="general", briefing_date=date(2026, 7, 23), fetch_status=status
        )
        self.assertIn("뉴스 수집 장애", briefing)
        self.assertNotIn("신규 채택 뉴스 없음", briefing)

    @patch("main.scrape_article_content", return_value="irrelevant body")
    @patch("main.parse_google_news_feed")
    @patch("main.requests.get", return_value=Mock())
    def test_rejected_firm_candidate_is_scraped_once_per_run(
        self, _mock_get, mock_parse_feed, mock_scrape
    ):
        entry = SimpleNamespace(
            title="한화 계열사 조직 개편 - 연합뉴스",
            link="https://example.com/repeated",
            published="2026-07-23",
        )
        mock_parse_feed.return_value = SimpleNamespace(entries=[entry])

        result = main.fetch_firm_mention_news("Baikal Investment")

        self.assertEqual(mock_scrape.call_count, 1)
        self.assertEqual(result[1], [])
        self.assertGreater(result[4]["queries_succeeded"], 1)


class MarketPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.history = pd.DataFrame(
            {"Close": [100.0, 102.0, 105.0, 110.0]},
            index=pd.to_datetime(["2026-07-13", "2026-07-14", "2026-07-17", "2026-07-20"]),
        )

    def test_weekend_uses_previous_week_baseline(self):
        result = main.calculate_market_performance(self.history, mode="sunday")
        self.assertEqual(result["period"], "weekly")
        self.assertAlmostEqual(result["pct_change"], 10.0)

    def test_weekday_uses_previous_close(self):
        result = main.calculate_market_performance(self.history, mode="weekday")
        self.assertEqual(result["period"], "daily")
        self.assertAlmostEqual(result["pct_change"], (110.0 - 105.0) / 105.0 * 100)


class GeminiConfigTests(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_default_models_are_current_and_do_not_include_25_pro(self):
        models = main.get_gemini_models()
        self.assertEqual(models[0], "gemini-3.6-flash")
        self.assertNotIn("gemini-2.5-pro", models)


class PefBriefingFormatTests(unittest.TestCase):
    def test_no_news_fallback_has_no_it_pmi_role_or_actions(self):
        status = main.new_fetch_status("pef")
        status.update({"queries_attempted": 1, "queries_succeeded": 1})

        briefing = main.generate_briefing(
            {},
            "",
            target="pef",
            briefing_date=date(2026, 7, 23),
            fetch_status=status,
        )

        self.assertIn("Baikal Investment GP 인사이트 브리핑", briefing)
        self.assertNotIn("IT PMI", briefing)
        self.assertNotIn("Day-1", briefing)
        self.assertNotIn("TSA", briefing)


class BondMarketTests(unittest.TestCase):
    def test_parses_dart_toc_and_bond_event(self):
        report_html = """
        <script>
        var node3 = {};
        node3['text'] = "1. 공모개요";
        node3['rcpNo'] = "20260720000318";
        node3['dcmNo'] = "11483351";
        node3['eleId'] = "8";
        node3['offset'] = "81409";
        node3['length'] = "19095";
        node3['dtd'] = "dart4.xsd";
        </script>
        """
        sections = main.parse_dart_toc_sections(report_html)
        overview_section = main.find_dart_toc_section(sections, "공모개요")
        self.assertEqual(overview_section["eleId"], "8")

        overview_html = """
        <table>
          <tr><td>전자등록총액</td><td>80,000,000,000</td></tr>
          <tr><td>평가결과등급</td><td>AA-(안정적)</td></tr>
          <tr><td>상 환 기 한</td><td>2028년 07월 30일</td></tr>
        </table>
        <p>본 사채는 2026년 07월 23일 09시에서 16시까지
        한국금융투자협회 K-Bond 시스템을 통해 실시하는 수요예측결과에 따라
        발행조건이 결정될 예정입니다.</p>
        <p>수요예측 결과에 따라 전자등록총액 합계 금 사천억원
        (\\400,000,000,000) 이하의 범위에서 증액할 수 있습니다.</p>
        <p>공모희망금리는 회사채 개별민평 수익률에
        -0.30%p. ~ +0.30%p.를 가산합니다.</p>
        <table>
          <tr><td>전자등록총액</td><td>120,000,000,000</td></tr>
          <tr><td>평가결과등급</td><td>AA-(안정적)</td></tr>
          <tr><td>상 환 기 한</td><td>2029년 07월 30일</td></tr>
        </table>
        """
        disclosure = {
            "issuer": "케이씨씨",
            "security_type": "무보증사채",
            "payment_date": date(2026, 7, 30),
            "receipt_date": date(2026, 7, 20),
            "rcp_no": "20260720000318",
            "report_url": "https://dart.example/report",
        }
        event = main.parse_dart_bond_event(disclosure, overview_html)

        self.assertEqual(event["demand_date"], date(2026, 7, 23))
        self.assertEqual(event["start_time"], "09:00")
        self.assertEqual(event["end_time"], "16:00")
        self.assertEqual(event["amount_eok"], 2000)
        self.assertEqual(event["max_amount_eok"], 4000)
        self.assertEqual(event["rating"], "AA-")
        self.assertEqual(event["term"], "2년/3년")
        self.assertEqual(event["rate_band"], "개별민평 -30~+30bp")

    def test_kofia_filters_and_aggregates_plain_public_bonds(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <root><message><proframeHeader><pfmResponseDtal/></proframeHeader>
        <BISComDspDatDTO><val1>국가철도공단채권453</val1><val3>20260723</val3>
        <val6>3000</val6><val9>4.28</val9></BISComDspDatDTO>
        <BISComDspDatDTO><val1>삼성카드2897</val1><val3>20260723</val3>
        <val6>400</val6><val9>-</val9></BISComDspDatDTO>
        <BISComDspDatDTO><val1>한국장학재단26-20(사)</val1><val3>20260723</val3>
        <val6>700</val6><val9>-</val9></BISComDspDatDTO>
        <BISComDspDatDTO><val1>코람코리츠 7(사모)</val1><val3>20260723</val3>
        <val6>50</val6><val9>5.7</val9></BISComDspDatDTO>
        <BISComDspDatDTO><val1>하나증권(DLB)2800</val1><val3>20260723</val3>
        <val6>100</val6><val9>-</val9></BISComDspDatDTO>
        <BISComDspDatDTO><val1>베뉴지 2EB</val1><val3>20260723</val3>
        <val6>376.2</val6><val9>-</val9></BISComDspDatDTO>
        </message></root>""".encode("utf-8")

        records, excluded_counts = main.parse_kofia_issuance_response(
            xml,
            include_exclusions=True,
        )
        categories, total = main.aggregate_kofia_issuance(records)

        self.assertEqual(len(records), 3)
        self.assertEqual(total, 4100)
        self.assertEqual(
            {item["issuer"] for item in categories["공사채"]},
            {"국가철도공단", "한국장학재단"},
        )
        self.assertEqual(categories["여전채"][0]["issuer"], "삼성카드")
        self.assertEqual(excluded_counts["mezzanine"], 1)

    def test_parses_nh_syndication_schedule_rows(self):
        pdf_text = """
하나에프앤아이  A+  1.5  300  1,500  3,000  NH/KB/한투/신한  개별 -30~+30  7/27(월)  8/4(화)
                          2  700
                          3  500
메리츠금융지주  AA0  2  800  1,500  2,800  NH/KB/한투/신한  개별 -30~+30  7/29(수)  8/6(목)
                          3  700
"""
        events = main.parse_nh_syndication_text(
            pdf_text,
            reference_date=date(2026, 7, 24),
            pdf_url="https://example.com/nh.pdf",
        )

        self.assertEqual(len(events), 2)
        hana, meritz = events
        self.assertEqual(hana["issuer"], "하나에프앤아이")
        self.assertEqual(hana["term"], "1.5/2/3년")
        self.assertEqual(hana["amount_eok"], 1500)
        self.assertEqual(hana["max_amount_eok"], 3000)
        self.assertEqual(hana["demand_date"], date(2026, 7, 27))
        self.assertEqual(meritz["issuer"], "메리츠금융지주")
        self.assertEqual(meritz["rating"], "AA0")
        self.assertEqual(meritz["amount_eok"], 1500)
        self.assertEqual(meritz["max_amount_eok"], 2800)

    def test_dart_event_wins_over_matching_nh_schedule(self):
        dart_event = {
            "source": "dart",
            "issuer": "하나에프앤아이",
            "demand_date": date(2026, 7, 27),
            "amount_eok": 1500,
            "report_url": "https://dart.example/hana",
        }
        nh_event = {
            "source": "nh_pdf",
            "issuer": "하나에프앤아이",
            "demand_date": date(2026, 7, 27),
            "amount_eok": 1400,
            "max_amount_eok": 3000,
            "report_url": "https://nh.example/list.pdf",
        }

        merged = main.merge_bond_demand_events([dart_event], [nh_event])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "dart")
        self.assertEqual(merged[0]["amount_eok"], 1500)
        self.assertEqual(merged[0]["max_amount_eok"], 3000)
        self.assertEqual(merged[0]["report_url"], "https://dart.example/hana")

    def test_section_includes_nh_only_upcoming_schedule(self):
        section = main.build_bond_market_section(
            {
                "enabled": True,
                "reference_date": date(2026, 7, 24),
                "dart": {"status": "empty", "items": []},
                "nh": {
                    "status": "ok",
                    "items": [{
                        "source": "nh_pdf",
                        "issuer": "메리츠금융지주",
                        "rating": "AA0",
                        "term": "2/3년",
                        "amount_eok": 1500,
                        "max_amount_eok": 2800,
                        "demand_date": date(2026, 7, 29),
                        "payment_date": date(2026, 8, 6),
                        "report_url": "https://example.com/nh.pdf",
                    }],
                    "pdf_url": "https://example.com/nh.pdf",
                },
                "kofia": {
                    "status": "empty",
                    "items": [],
                    "categories": {},
                    "excluded_counts": {"mezzanine": 1},
                },
            }
        )

        self.assertIn("메리츠금융지주", section)
        self.assertIn("(NH 예정)", section)
        self.assertIn("최대 2,800억원", section)
        self.assertIn("메자닌(CB·BW·EB) 1건", section)
        self.assertNotIn("베뉴지", section)

    def test_section_distinguishes_empty_data_from_collection_error(self):
        section = main.build_bond_market_section(
            {
                "enabled": True,
                "reference_date": date(2026, 7, 23),
                "dart": {"status": "empty", "items": []},
                "kofia": {"status": "error", "items": [], "categories": {}},
            }
        )

        self.assertIn("확인된 신규 수요예측 일정이 없습니다", section)
        self.assertIn("수집 실패로 금일 발행 여부를 판단할 수 없습니다", section)
        self.assertNotIn("<b>공사채</b>", section)


if __name__ == "__main__":
    unittest.main()
