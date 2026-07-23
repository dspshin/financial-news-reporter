import unittest
from datetime import date
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
    @patch("main.save_news_history")
    @patch("main.send_telegram_message", side_effect=[True, True, False])
    @patch("main.generate_briefing", side_effect=["<b>general</b>", "<b>pef</b>"])
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
        _mock_generate,
        _mock_send,
        mock_save,
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


if __name__ == "__main__":
    unittest.main()
