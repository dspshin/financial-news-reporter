import unittest
import json
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
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

    def test_accepts_tender_offer_spa_and_gp_commitment_headlines(self):
        deal_body = (
            "TPG가 롯데렌탈 지분을 인수하고 잔여 지분 공개매수와 "
            "주식매매계약 종결 절차를 진행하는 거래입니다. "
        ) * 10
        tender_offer = main.evaluate_pef_article(
            "TPG, 롯데렌탈 품는다…잔여지분 공개매수도 착수 - 매일경제",
            "https://example.com/tender-offer",
            deal_body,
        )
        spa = main.evaluate_pef_article(
            "美 TPG, 전량 에쿼티로 롯데렌탈 산다…1.3조에 SPA 체결 - 한국경제",
            "https://example.com/spa",
            deal_body,
        )
        gp_commitment = main.evaluate_pef_article(
            "신한벤처, 모펀드 GP 11곳에 400억 매칭 출자 - 딜사이트",
            "https://example.com/gp-commitment",
            "모펀드가 GP를 선정하고 운용사에 매칭 출자하는 출자사업 내용입니다. " * 10,
        )

        self.assertTrue(tender_offer["accepted"])
        self.assertTrue(spa["accepted"])
        self.assertTrue(gp_commitment["accepted"])
        self.assertTrue(
            main.get_pef_headline_candidate_hits(
                "TPG, 롯데렌탈 품는다…잔여지분 공개매수도 착수"
            )["accepted"]
        )

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

    @patch.dict("os.environ", {"PEF_MIN_BODY_CHARS": "160"}, clear=False)
    def test_rejects_missing_short_and_access_blocked_bodies(self):
        title = "KKR, 국내 소프트웨어 기업 경영권 인수 본입찰 - 더벨"
        missing = main.evaluate_pef_article(title, "https://example.com/missing", None)
        short = main.evaluate_pef_article(
            title,
            "https://example.com/short",
            "경영권 인수 기사 요약",
        )
        blocked = main.evaluate_pef_article(
            title,
            "https://example.com/blocked",
            "로그인 후 이용 가능한 유료회원 전용 기사입니다. " * 20,
        )

        self.assertFalse(missing["accepted"])
        self.assertFalse(short["accepted"])
        self.assertFalse(blocked["accepted"])
        self.assertIn("missing_content", missing["reasons"])
        self.assertTrue(any(reason.startswith("content_too_short") for reason in short["reasons"]))
        self.assertTrue(any(reason.startswith("content_access_blocked") for reason in blocked["reasons"]))

    def test_accepts_specialist_deal_vocabulary_only_with_confirming_body(self):
        body = (
            "사모펀드 하일랜드EP가 인수한 샐러디의 매각을 준비하며 "
            "멀티브랜드 확장을 통한 밸류업과 투자금 회수를 검토하고 있습니다. "
        ) * 8
        accepted = main.evaluate_pef_article(
            "하일랜드EP, 샐러디 밸류업 '멀티브랜드 확장'에 달렸다 - 더벨",
            "https://www.thebell.co.kr/example",
            body,
            specialist_query=True,
        )
        unrelated = main.evaluate_pef_article(
            "샐러디, 여름 신제품 출시 행사 개최 - 더벨",
            "https://www.thebell.co.kr/unrelated",
            "샐러드 신제품과 할인 프로모션을 소개하는 소비자 기사입니다. " * 10,
            specialist_query=True,
        )
        body_only_signal = main.evaluate_pef_article(
            "트리니티항공, MRO 진출 제동…격납고 투자 또 연기 - 딜사이트",
            "https://dealsite.co.kr/unrelated-body-signal",
            (
                "항공 정비 격납고 투자 일정이 연기됐다는 기사입니다. "
                "페이지 하단에는 다른 기업의 M&A와 경영권 인수 관련 기사도 노출됩니다. "
            ) * 8,
            specialist_query=True,
        )
        training_course = main.evaluate_pef_article(
            "한국능률협회, 제5회 M&A지도사 전문가 과정 개설 - 교육신문",
            "https://example.com/training",
            "M&A 실무 교육과 자격증 과정을 소개하는 모집 기사입니다. " * 10,
            specialist_query=True,
        )

        self.assertTrue(accepted["accepted"])
        self.assertFalse(unrelated["accepted"])
        self.assertFalse(body_only_signal["accepted"])
        self.assertFalse(training_course["accepted"])
        self.assertTrue(accepted["content_accessible"])
        self.assertTrue(any(reason.startswith("specialist_body") for reason in accepted["reasons"]))


class GoogleNewsResolverTests(unittest.TestCase):
    def setUp(self):
        main._GOOGLE_NEWS_URL_CACHE.clear()
        main._LAST_GOOGLE_NEWS_RESOLVE_AT = 0.0
        main._GOOGLE_NEWS_RATE_LIMIT_UNTIL = 0.0

    @staticmethod
    def _response(content=b"", text="", url="https://news.google.com/"):
        response = Mock()
        response.content = content
        response.text = text
        response.url = url
        response.raise_for_status.return_value = None
        return response

    def test_decodes_current_google_news_token_with_batch_rpc(self):
        token = "CBMiResolverToken123"
        google_url = f"https://news.google.com/rss/articles/{token}?oc=5"
        parameter_html = (
            '<c-wiz><div jscontroller="abc" data-n-a-sg="signature-123" '
            'data-n-a-ts="1786413525"></div></c-wiz>'
        ).encode("utf-8")
        batch_text = (
            ")]}'\n\n"
            '[["wrb.fr","Fbv4je","[\\"garturlres\\",'
            '\\"https://publisher.example.com/article/123\\"]"]]\n'
        )
        requester = Mock()
        requester.get.return_value = self._response(
            content=parameter_html,
            url=f"https://news.google.com/articles/{token}?hl=ko",
        )
        requester.post.return_value = self._response(text=batch_text)

        decoded = main.resolve_google_news_url(google_url, requester=requester)
        cached = main.resolve_google_news_url(google_url, requester=requester)

        self.assertEqual(decoded, "https://publisher.example.com/article/123")
        self.assertEqual(cached, decoded)
        self.assertEqual(requester.get.call_count, 1)
        requester.post.assert_called_once()
        posted_payload = requester.post.call_args.kwargs["data"]["f.req"]
        self.assertIn(token, posted_payload)
        self.assertIn("signature-123", posted_payload)

    def test_direct_publisher_url_does_not_call_google(self):
        requester = Mock()
        url = "https://publisher.example.com/article/123"

        self.assertEqual(main.resolve_google_news_url(url, requester=requester), url)
        requester.get.assert_not_called()
        requester.post.assert_not_called()

    @patch("main.resolve_google_news_url")
    def test_scraper_uses_resolved_url_and_extracts_article_paragraphs(self, resolver):
        resolver.return_value = "https://publisher.example.com/article/123"
        article_html = """
        <html><body>
          <nav><p>메뉴 메뉴 메뉴 메뉴 메뉴 메뉴 메뉴 메뉴 메뉴 메뉴</p></nav>
          <article>
            <p>사모펀드가 경영권 인수를 추진하며 본입찰 참여자와 거래 조건을 검토하고 있습니다.</p>
            <p>인수금융 구조와 향후 밸류업 전략, 투자금 회수 시나리오도 함께 논의되고 있습니다.</p>
            <p>거래 종결 전에는 실사 결과와 규제 승인 여부를 추가로 확인해야 한다는 설명입니다.</p>
          </article>
          <footer><p>Copyright All rights reserved. 무단 전재 및 재배포 금지</p></footer>
        </body></html>
        """.encode("utf-8")
        requester = Mock()
        requester.get.return_value = self._response(
            content=article_html,
            url="https://publisher.example.com/article/123",
        )
        google_url = "https://news.google.com/rss/articles/test-token?oc=5"

        result = main.scrape_article_content(
            google_url,
            return_metadata=True,
            requester=requester,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["resolved_url"], "https://publisher.example.com/article/123")
        self.assertIn("사모펀드가 경영권 인수", result["content"])
        self.assertNotIn("메뉴 메뉴", result["content"])
        self.assertNotIn("Copyright", result["content"])

    def test_google_wrapper_without_article_body_is_not_accepted(self):
        content = main.extract_article_body(
            b"<html><head><title>Google News</title></head><body></body></html>"
        )
        self.assertIsNone(content)

    @patch.dict(
        "os.environ",
        {
            "GOOGLE_NEWS_RESOLVE_INTERVAL_SECONDS": "0",
            "GOOGLE_NEWS_RATE_LIMIT_COOLDOWN_SECONDS": "30",
        },
        clear=False,
    )
    def test_rate_limit_opens_circuit_without_hammering_google(self):
        token = "CBMiRateLimitedToken"
        google_url = f"https://news.google.com/rss/articles/{token}?oc=5"
        rate_limited = self._response(
            content=b"rate limited",
            url="https://www.google.com/sorry/",
        )
        rate_limited.status_code = 429
        requester = Mock()
        requester.get.return_value = rate_limited

        with self.assertRaisesRegex(
            main.GoogleNewsResolutionError,
            "google_news_rate_limited",
        ):
            main.resolve_google_news_url(google_url, requester=requester)
        with self.assertRaisesRegex(
            main.GoogleNewsResolutionError,
            "circuit_open",
        ):
            main.resolve_google_news_url(google_url, requester=requester)

        self.assertEqual(requester.get.call_count, 1)


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

    def test_matches_same_deal_across_specialist_headline_styles(self):
        first = "KDB생명 3파전 압축, 롯데렌탈 딜 향방 촉각 - 더벨"
        second = "KDB생명 본입찰에 한화·흥국·한투 참여…유효경쟁 성립 - 인베스트조선"
        self.assertTrue(main.is_same_news_event(first, second))


class NewsLinkClusteringTests(unittest.TestCase):
    def test_clustered_message_preserves_all_source_links(self):
        links = [
            (
                "KDB생명 3파전 압축, 롯데렌탈 딜 향방 촉각 - 더벨",
                "https://example.com/thebell",
            ),
            (
                "KDB생명 본입찰에 한화·흥국·한투 참여…유효경쟁 성립 - 인베스트조선",
                "https://example.com/investchosun",
            ),
        ]

        message = main.build_news_links_message(links, cluster_events=True)

        self.assertIn("2개 출처", message)
        self.assertIn("https://example.com/thebell", message)
        self.assertIn("https://example.com/investchosun", message)
        self.assertIn(">더벨</a>", message)
        self.assertIn(">인베스트조선</a>", message)


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

    def test_bond_history_commits_only_after_complete_delivery(self):
        pending_history = main.build_bond_history_state(
            events={"issuer|regular": {"event": {"issuer": "발행사"}}},
            path="/tmp/bond-history-test.json",
            initialized=True,
        )
        digest = {"next_history": pending_history}
        saver = Mock(return_value=True)

        self.assertFalse(main.commit_bond_history_after_delivery(
            digest,
            delivery_configured=True,
            delivery_complete=False,
            saver=saver,
        ))
        saver.assert_not_called()

        self.assertTrue(main.commit_bond_history_after_delivery(
            digest,
            delivery_configured=True,
            delivery_complete=True,
            saver=saver,
        ))
        saver.assert_called_once_with(pending_history)

    @patch.dict(
        "os.environ",
        {
            "TELEGRAM_PEF_CHANNEL_ID": "@pef",
            "EMAIL_ENABLED": "true",
            "EMAIL_GENERAL_TO": "general@example.com",
            "EMAIL_PEF_TO": "pef@example.com",
        },
        clear=False,
    )
    @patch("main.setup_logging")
    @patch("main.check_holidays", return_value=(False, False, None, None))
    @patch("main.wait_until_pef_start")
    @patch("main.save_news_history")
    @patch("main.send_email_message")
    @patch("main.send_telegram_message")
    @patch("main.generate_briefing", side_effect=["<b>general</b>", "<b>pef</b>"])
    @patch("main.fetch_bond_market_data", return_value={"enabled": False})
    @patch("main.load_pef_watchlist", return_value=[])
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
        _mock_load_watchlist,
        _mock_bond_market,
        _mock_generate,
        mock_send_telegram,
        mock_send_email,
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
        delivery_order = []
        telegram_results = iter([True, True, False])

        def send_telegram(_message, target="general"):
            delivery_order.append(("telegram", target))
            return next(telegram_results)

        def send_email(_message, target="general", briefing_date=None):
            delivery_order.append(("email", target))
            return True

        mock_send_telegram.side_effect = send_telegram
        mock_send_email.side_effect = send_email

        main.main()

        self.assertEqual([item["target"] for item in history["articles"]], ["general"])
        mock_save.assert_called_once_with(history)
        self.assertEqual(
            delivery_order,
            [
                ("telegram", "general"),
                ("email", "general"),
                ("telegram", "pef"),
                ("telegram", "pef"),
                ("email", "pef"),
            ],
        )
        pef_email_body = mock_send_email.call_args_list[1].args[0]
        self.assertIn("PEF deal", pef_email_body)


class EmailNotifierTests(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {
            "EMAIL_ENABLED": "true",
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
            "SMTP_SECURITY": "starttls",
            "SMTP_USERNAME": "sender@example.com",
            "SMTP_PASSWORD": "test-password",
            "EMAIL_FROM": "sender@example.com",
            "EMAIL_FROM_NAME": "Briefing Bot",
            "EMAIL_GENERAL_TO": "alpha@example.com; beta@example.com",
            "EMAIL_SUBJECT_PREFIX": "[Daily]",
            "EMAIL_MAX_ATTEMPTS": "1",
        },
        clear=True,
    )
    @patch("main.ssl.create_default_context")
    @patch("main.smtplib.SMTP")
    def test_sends_multipart_html_email_with_starttls(
        self,
        mock_smtp,
        mock_create_context,
    ):
        server = mock_smtp.return_value.__enter__.return_value
        server.send_message.return_value = {}

        sent = main.send_email_message(
            (
                "<b>브리핑 제목</b>\n\n"
                "- 핵심 내용\n"
                "<a href='https://example.com/news'>뉴스 링크</a>\n"
                "<script>unsafe()</script>"
            ),
            target="general",
            briefing_date=date(2026, 7, 29),
        )

        self.assertTrue(sent)
        mock_smtp.assert_called_once_with(
            "smtp.example.com",
            587,
            timeout=30,
        )
        server.starttls.assert_called_once_with(
            context=mock_create_context.return_value
        )
        server.login.assert_called_once_with("sender@example.com", "test-password")
        email_message = server.send_message.call_args.args[0]
        self.assertEqual(
            email_message["Subject"],
            "[Daily] 07/29(Wed) 데일리 경제 브리핑",
        )
        self.assertEqual(
            email_message["To"],
            "alpha@example.com, beta@example.com",
        )
        self.assertTrue(email_message.is_multipart())
        plain_body = email_message.get_body(preferencelist=("plain",)).get_content()
        html_body = email_message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("뉴스 링크 (https://example.com/news)", plain_body)
        self.assertNotIn("unsafe()", plain_body)
        self.assertIn("<b>브리핑 제목</b>", html_body)
        self.assertIn('href="https://example.com/news"', html_body)
        self.assertNotIn("<script>", html_body)

    @patch.dict(
        "os.environ",
        {
            "EMAIL_ENABLED": "false",
            "SMTP_HOST": "smtp.example.com",
            "EMAIL_GENERAL_TO": "alpha@example.com",
        },
        clear=True,
    )
    @patch("main.smtplib.SMTP")
    def test_disabled_email_does_not_open_smtp_connection(self, mock_smtp):
        self.assertFalse(main.send_email_message("<b>briefing</b>"))
        mock_smtp.assert_not_called()

    @patch.dict(
        "os.environ",
        {
            "EMAIL_ENABLED": "true",
            "EMAIL_TO": "",
            "EMAIL_GENERAL_TO": "",
            "EMAIL_PEF_TO": "pef@example.com",
        },
        clear=True,
    )
    @patch("main.smtplib.SMTP")
    @patch("main.smtplib.SMTP_SSL")
    def test_empty_general_recipients_skip_without_smtp_or_error(
        self,
        mock_smtp_ssl,
        mock_smtp,
    ):
        self.assertFalse(main.email_target_enabled("general"))
        self.assertTrue(main.email_target_enabled("pef"))
        self.assertFalse(
            main.send_email_message("<b>general briefing</b>", target="general")
        )
        mock_smtp.assert_not_called()
        mock_smtp_ssl.assert_not_called()


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


class NewsScheduleTests(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_monday_uses_weekend_catchup_defaults(self):
        monday = date(2026, 8, 10)

        settings = main.get_news_fetch_settings("weekday", monday)
        queries = main.build_news_queries(
            mode="weekday",
            target="general",
            reference_date=monday,
        )

        self.assertEqual(settings["lookback_days"], 3)
        self.assertEqual(settings["max_entries_per_query"], 5)
        self.assertTrue(settings["monday_catchup"])
        self.assertIn("주말 글로벌 경제 뉴스", queries)
        self.assertIn("이번주 증시 일정", queries)
        self.assertIn("이번주 경제 캘린더", queries)

    @patch.dict("os.environ", {}, clear=True)
    def test_non_monday_keeps_daily_defaults(self):
        tuesday = date(2026, 8, 11)

        settings = main.get_news_fetch_settings("weekday", tuesday)
        queries = main.build_news_queries(
            mode="weekday",
            target="general",
            reference_date=tuesday,
        )

        self.assertEqual(settings["lookback_days"], 1)
        self.assertEqual(settings["max_entries_per_query"], 3)
        self.assertFalse(settings["monday_catchup"])
        self.assertNotIn("주말 글로벌 경제 뉴스", queries)

    @patch.dict("os.environ", {}, clear=True)
    def test_pef_queries_prioritize_specialist_media_by_default(self):
        queries = main.build_news_queries(
            mode="weekday",
            target="pef",
            reference_date=date(2026, 8, 11),
        )

        self.assertEqual(
            queries[:len(main.PEF_SPECIALIST_NEWS_QUERIES)],
            list(main.PEF_SPECIALIST_NEWS_QUERIES),
        )
        self.assertIn("사모펀드", queries)

    @patch.dict(
        "os.environ",
        {"PEF_SPECIALIST_NEWS_ENABLED": "false"},
        clear=True,
    )
    def test_pef_specialist_queries_can_be_disabled(self):
        queries = main.build_news_queries(
            mode="weekday",
            target="pef",
            reference_date=date(2026, 8, 11),
        )

        self.assertFalse(any(query.startswith("site:") for query in queries))

    @patch.dict("os.environ", {}, clear=True)
    @patch("main.scrape_article_content", return_value="article body")
    @patch("main.parse_google_news_feed")
    @patch("main.requests.get", return_value=Mock())
    def test_monday_fetch_uses_three_day_window_and_five_entries(
        self,
        mock_get,
        mock_parse_feed,
        _mock_scrape,
    ):
        entries = [
            SimpleNamespace(
                title=f"월요일 뉴스 {index} - 연합뉴스",
                link=f"https://example.com/monday-{index}",
                published="2026-08-10",
            )
            for index in range(6)
        ]
        mock_parse_feed.return_value = SimpleNamespace(entries=entries)

        _context, links, _seen, pending, status = main.fetch_news(
            mode="weekday",
            target="general",
            collected_date=date(2026, 8, 10),
        )

        first_query = mock_get.call_args_list[0].kwargs["params"]["q"]
        self.assertIn("when:3d", first_query)
        self.assertEqual(len(links), 5)
        self.assertEqual(len(pending), 5)
        self.assertEqual(status["queries_attempted"], 6)

    @patch.dict(
        "os.environ",
        {
            "PEF_SPECIALIST_NEWS_ENABLED": "false",
            "PEF_MAX_UNIQUE_DEALS": "1",
        },
        clear=False,
    )
    @patch(
        "main.scrape_article_content",
        return_value="사모펀드가 경영권 인수를 추진하는 거래 관련 기사 본문입니다. " * 10,
    )
    @patch("main.parse_google_news_feed")
    @patch("main.requests.get", return_value=Mock())
    def test_pef_unique_deal_limit_keeps_overflow_as_links(
        self,
        _mock_get,
        mock_parse_feed,
        _mock_scrape,
    ):
        first = SimpleNamespace(
            title="KKR, A사 경영권 인수 본입찰 - 더벨",
            link="https://example.com/a",
            published="2026-08-11",
        )
        second = SimpleNamespace(
            title="MBK, B사 경영권 인수 본입찰 - 딜사이트",
            link="https://example.com/b",
            published="2026-08-11",
        )
        mock_parse_feed.return_value = SimpleNamespace(entries=[first, second])

        context, links, _seen, pending, _status = main.fetch_news(
            mode="weekday",
            target="pef",
            collected_date=date(2026, 8, 11),
        )

        self.assertIn(first.title, context)
        self.assertNotIn(second.title, context)
        self.assertEqual(links, [(first.title, first.link), (second.title, second.link)])
        self.assertEqual(len(pending), 2)


class PefWatchlistTests(unittest.TestCase):
    def test_default_watchlist_includes_sewoo_global(self):
        watchlist_path = Path(__file__).with_name("pef_watchlist.json")

        watchlist = main.load_pef_watchlist(str(watchlist_path))

        self.assertIn("세우글로벌", [company["name"] for company in watchlist])

    def test_loads_and_normalizes_watchlist_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            watchlist_path = Path(temp_dir) / "watchlist.json"
            watchlist_path.write_text(
                json.dumps(
                    [
                        {"name": " 모토닉 ", "aliases": ["모토닉", " Motonic "]},
                        {
                            "name": "페퍼저축은행",
                            "aliases": ["페퍼 저축은행", "Pepper Savings Bank"],
                        },
                        {"name": 1234, "aliases": ["invalid"]},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            watchlist = main.load_pef_watchlist(str(watchlist_path))

        self.assertEqual(
            watchlist,
            [
                {"name": "모토닉", "aliases": ["모토닉", "Motonic"]},
                {
                    "name": "페퍼저축은행",
                    "aliases": [
                        "페퍼저축은행",
                        "페퍼 저축은행",
                        "Pepper Savings Bank",
                    ],
                },
            ],
        )

    @patch.dict(
        "os.environ",
        {
            "PEF_WATCHLIST_MAX_ARTICLES_PER_COMPANY": "1",
            "PEF_WATCHLIST_MAX_CANDIDATES_PER_QUERY": "5",
        },
        clear=False,
    )
    @patch(
        "main.scrape_article_content",
        return_value="관심 기업의 신규 사업과 재무 현황, 주요 경영 변화를 설명하는 기사 본문입니다. " * 10,
    )
    @patch("main.parse_google_news_feed")
    @patch("main.requests.get", return_value=Mock())
    def test_collects_and_groups_news_by_watchlist_company(
        self,
        mock_get,
        mock_parse_feed,
        _mock_scrape,
    ):
        mock_parse_feed.side_effect = [
            SimpleNamespace(entries=[SimpleNamespace(
                title="모토닉, 신규 사업 확대 - 연합뉴스",
                link="https://example.com/monotic",
                published="2026-08-08",
            )]),
            SimpleNamespace(entries=[SimpleNamespace(
                title="페퍼저축은행, 건전성 관리 강화 - 한국경제",
                link="https://example.com/pepper",
                published="2026-08-08",
            )]),
        ]
        watchlist = [
            {"name": "모토닉", "aliases": ["모토닉"]},
            {"name": "페퍼저축은행", "aliases": ["페퍼저축은행"]},
        ]

        context, links, seen, pending, status = main.fetch_watchlist_news(
            watchlist,
            collected_date=date(2026, 8, 8),
        )
        links_message = main.build_watchlist_links_message(links)

        self.assertEqual([item["company"] for item in links], ["모토닉", "페퍼저축은행"])
        self.assertEqual({item["target"] for item in pending}, {"pef_watchlist"})
        self.assertEqual(seen, {"https://example.com/monotic", "https://example.com/pepper"})
        self.assertEqual(status["queries_attempted"], 2)
        self.assertIn("Watchlist Company: 모토닉", context)
        self.assertIn("Watchlist Company: 페퍼저축은행", context)
        self.assertIn("<b>모토닉</b>", links_message)
        self.assertIn("<b>페퍼저축은행</b>", links_message)
        self.assertIn("when:1d", mock_get.call_args_list[0].kwargs["params"]["q"])

    def test_empty_watchlist_links_omit_message(self):
        self.assertIsNone(main.build_watchlist_links_message([]))


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

    @patch.dict(
        "os.environ",
        {"PEF_SPECIALIST_NEWS_ENABLED": "false"},
        clear=False,
    )
    @patch(
        "main.scrape_article_content",
        return_value={
            "content": None,
            "resolved_url": None,
            "status": "google_news_unresolved",
            "error": "decoding_parameters_not_found",
        },
    )
    @patch("main.parse_google_news_feed")
    @patch("main.requests.get", return_value=Mock())
    def test_article_resolution_outage_is_not_reported_as_no_news(
        self,
        _mock_get,
        mock_parse_feed,
        _mock_scrape,
    ):
        entry = SimpleNamespace(
            title="KKR, A사 경영권 인수 본입찰 - 더벨",
            link="https://news.google.com/rss/articles/unresolved?oc=5",
            published="2026-08-11",
        )
        mock_parse_feed.return_value = SimpleNamespace(entries=[entry])

        context, links, _seen, pending, status = main.fetch_news(
            mode="weekday",
            target="pef",
            collected_date=date(2026, 8, 11),
        )
        briefing = main.generate_briefing(
            {},
            context,
            target="pef",
            briefing_date=date(2026, 8, 11),
            fetch_status=status,
        )

        self.assertEqual(context, "")
        self.assertEqual(links, [])
        self.assertEqual(pending, [])
        self.assertTrue(main.is_content_fetch_outage(status))
        self.assertIn("기사 본문 수집 장애", briefing)
        self.assertNotIn("신규 채택 뉴스 없음", briefing)

    @patch.dict(
        "os.environ",
        {"PEF_SPECIALIST_NEWS_ENABLED": "false"},
        clear=False,
    )
    @patch("main.scrape_article_content")
    @patch("main.parse_google_news_feed")
    @patch("main.requests.get", return_value=Mock())
    def test_pef_prefilter_skips_body_fetch_without_headline_anchor(
        self,
        _mock_get,
        mock_parse_feed,
        mock_scrape,
    ):
        entry = SimpleNamespace(
            title="트리니티항공, MRO 격납고 투자 일정 연기 - 딜사이트",
            link="https://news.google.com/rss/articles/no-anchor?oc=5",
            published="2026-08-11",
        )
        mock_parse_feed.return_value = SimpleNamespace(entries=[entry])

        context, links, _seen, pending, status = main.fetch_news(
            mode="weekday",
            target="pef",
            collected_date=date(2026, 8, 11),
        )

        self.assertEqual(context, "")
        self.assertEqual(links, [])
        self.assertEqual(pending, [])
        self.assertEqual(status["content_attempted"], 0)
        mock_scrape.assert_not_called()


class PefFetchSelectionTests(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {"PEF_SPECIALIST_NEWS_ENABLED": "false"},
        clear=False,
    )
    @patch(
        "main.scrape_article_content",
        return_value={
            "content": "사모펀드가 기업 경영권 인수를 추진하며 본입찰 조건을 검토하고 있습니다. " * 10,
            "resolved_url": "https://publisher.example.com/deal/123",
            "status": "ok",
            "error": None,
        },
    )
    @patch("main.parse_google_news_feed")
    @patch("main.requests.get", return_value=Mock())
    def test_pef_links_and_history_use_resolved_publisher_url(
        self,
        _mock_get,
        mock_parse_feed,
        _mock_scrape,
    ):
        entry = SimpleNamespace(
            title="KKR, A사 경영권 인수 본입찰 - 더벨",
            link="https://news.google.com/rss/articles/google-token?oc=5",
            published="2026-08-11",
        )
        mock_parse_feed.return_value = SimpleNamespace(entries=[entry])

        _context, links, _seen, pending, status = main.fetch_news(
            mode="weekday",
            target="pef",
            collected_date=date(2026, 8, 11),
        )

        self.assertEqual(
            links,
            [(entry.title, "https://publisher.example.com/deal/123")],
        )
        self.assertEqual(pending[0]["link"], "https://publisher.example.com/deal/123")
        self.assertEqual(status["content_usable"], 1)
        self.assertFalse(status["content_outage"])

    @patch.dict(
        "os.environ",
        {"PEF_SPECIALIST_NEWS_ENABLED": "false"},
        clear=False,
    )
    @patch(
        "main.scrape_article_content",
        return_value={
            "content": "TPG가 롯데렌탈 경영권 인수와 공개매수를 추진하는 거래 기사입니다. " * 10,
            "resolved_url": "https://publisher.example.com/tpg-lotte-rental",
            "status": "ok",
            "error": None,
        },
    )
    @patch("main.parse_google_news_feed")
    @patch("main.requests.get", return_value=Mock())
    def test_pef_skips_same_headline_and_source_before_url_resolution(
        self,
        _mock_get,
        mock_parse_feed,
        mock_scrape,
    ):
        title = "TPG, 롯데렌탈 품는다…잔여지분 공개매수도 착수 - 매일경제"
        first = SimpleNamespace(
            title=title,
            link="https://news.google.com/rss/articles/token-one?oc=5",
            published="2026-08-11",
        )
        duplicate = SimpleNamespace(
            title=title,
            link="https://news.google.com/rss/articles/token-two?oc=5",
            published="2026-08-11",
        )
        mock_parse_feed.return_value = SimpleNamespace(entries=[first, duplicate])

        context, links, _seen, pending, status = main.fetch_news(
            mode="weekday",
            target="pef",
            collected_date=date(2026, 8, 11),
        )

        self.assertIn("롯데렌탈", context)
        self.assertEqual(len(links), 1)
        self.assertEqual(len(pending), 1)
        self.assertEqual(status["content_attempted"], 1)
        mock_scrape.assert_called_once()

    @patch.dict(
        "os.environ",
        {
            "PEF_SPECIALIST_NEWS_ENABLED": "true",
            "PEF_MAX_SOURCES_PER_DEAL": "3",
        },
        clear=False,
    )
    @patch(
        "main.scrape_article_content",
        return_value=(
            "KDB생명 경영권 매각 본입찰에 사모펀드와 금융회사가 참여했으며 "
            "인수 후보 간 유효경쟁이 성립한 거래 관련 기사입니다. " * 8
        ),
    )
    @patch("main.parse_google_news_feed")
    @patch("main.requests.get", return_value=Mock())
    def test_same_pef_deal_keeps_corroborating_source_and_link(
        self,
        _mock_get,
        mock_parse_feed,
        _mock_scrape,
    ):
        first = SimpleNamespace(
            title="KDB생명 3파전 압축, 롯데렌탈 딜 향방 촉각 - 더벨",
            link="https://example.com/thebell-kdb",
            published="2026-08-10",
        )
        second = SimpleNamespace(
            title="KDB생명 본입찰에 한화·흥국·한투 참여…유효경쟁 성립 - 인베스트조선",
            link="https://example.com/investchosun-kdb",
            published="2026-08-10",
        )
        mock_parse_feed.return_value = SimpleNamespace(entries=[first, second])

        context, links, _seen, pending, _status = main.fetch_news(
            mode="weekday",
            target="pef",
            collected_date=date(2026, 8, 10),
        )

        self.assertIn("--- CORROBORATING ARTICLE START ---", context)
        self.assertEqual(links, [(first.title, first.link), (second.title, second.link)])
        self.assertEqual(len(pending), 2)


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
        self.assertNotIn("관심 기업 뉴스 레이더", briefing)

    @patch.dict(
        "os.environ",
        {
            "GEMINI_API_KEY": "test-key",
            "GEMINI_MODELS": "test-model",
        },
        clear=False,
    )
    @patch("main.genai.Client")
    def test_watchlist_prompt_section_is_added_only_for_watchlist_articles(
        self,
        mock_client,
    ):
        generate_content = mock_client.return_value.models.generate_content
        generate_content.return_value = SimpleNamespace(text="<b>briefing</b>")

        main.generate_briefing(
            {},
            "Title: 일반 PEF 기사\nContent: 일반 기사 본문",
            target="pef",
            briefing_date=date(2026, 8, 8),
        )
        main.generate_briefing(
            {},
            (
                "--- WATCHLIST ARTICLE START ---\n"
                "Watchlist Company: 모토닉\n"
                "Title: 모토닉 신규 사업\n"
                "--- WATCHLIST ARTICLE END ---"
            ),
            target="pef",
            briefing_date=date(2026, 8, 8),
        )

        regular_prompt = generate_content.call_args_list[0].kwargs["contents"]
        watchlist_prompt = generate_content.call_args_list[1].kwargs["contents"]
        self.assertNotIn("관심 기업 뉴스 레이더", regular_prompt)
        self.assertIn("관심 기업 뉴스 레이더", watchlist_prompt)
        self.assertIn("Watchlist Company: 모토닉", watchlist_prompt)


class BondMarketTests(unittest.TestCase):
    @staticmethod
    def _schedule_event(
        issuer,
        demand_date,
        payment_date,
        amount_eok=500,
        max_amount_eok=1000,
    ):
        return {
            "source": "nh_pdf",
            "issuer": issuer,
            "rating": "A0",
            "term": "2년",
            "amount_eok": amount_eok,
            "max_amount_eok": max_amount_eok,
            "demand_date": demand_date,
            "payment_date": payment_date,
            "managers": ["NH", "KB"],
            "tranches": [{"term": "2", "amount_eok": amount_eok}],
            "rate_band": "개별 -30~+30bp",
            "report_url": "https://example.com/nh.pdf",
        }

    @staticmethod
    def _bond_market_data(reference_date, events, nh_status="ok", source_date=None):
        return {
            "enabled": True,
            "reference_date": reference_date,
            "dart": {"status": "empty", "items": []},
            "kofia": {
                "status": "empty",
                "items": [],
                "pending_items": [],
                "categories": {},
                "pending_categories": {},
            },
            "nh": {
                "status": nh_status,
                "source_date": source_date or reference_date,
                "items": events,
                "pdf_url": "https://example.com/nh.pdf",
            },
        }

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
          <tr><td>평가결과등급</td><td>AA0(안정적)</td></tr>
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
          <tr><td>평가결과등급</td><td>AA0(안정적)</td></tr>
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
        self.assertEqual(event["rating"], "AA0")
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
        <BISComDspDatDTO><val1>한국철도공사284</val1><val3>20260723</val3>
        <val6>0</val6><val9>-</val9></BISComDspDatDTO>
        <BISComDspDatDTO><val1>한국투자증권34</val1><val3>20260723</val3>
        <val6>0</val6><val9>-</val9></BISComDspDatDTO>
        <BISComDspDatDTO><val1>졸스37</val1><val3>20260723</val3>
        <val6>0</val6><val9>-</val9></BISComDspDatDTO>
        <BISComDspDatDTO><val1>통화안정증권DC026-1110-0910</val1><val3>20260723</val3>
        <val6>5000</val6><val9>-</val9></BISComDspDatDTO>
        </message></root>""".encode("utf-8")

        records, pending_records, excluded_counts = main.parse_kofia_issuance_response(
            xml,
            include_exclusions=True,
            include_pending=True,
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
        self.assertEqual(excluded_counts["non_target"], 1)
        self.assertEqual(categories["회사채"], [])
        self.assertEqual(
            [record["issuer"] for record in pending_records],
            ["한국철도공사", "한국투자증권"],
        )

    @patch.dict(
        "os.environ",
        {"BOND_KOFIA_PENDING_COMPANY_ALLOWLIST": "코웨이"},
        clear=False,
    )
    def test_kofia_pending_company_allowlist_keeps_named_issuer(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <root><message><proframeHeader><pfmResponseDtal/></proframeHeader>
        <BISComDspDatDTO><val1>코웨이15</val1><val3>20260729</val3>
        <val6>0</val6></BISComDspDatDTO>
        </message></root>""".encode("utf-8")

        _records, pending_records = main.parse_kofia_issuance_response(
            xml,
            include_pending=True,
        )

        self.assertEqual(
            [record["issuer"] for record in pending_records],
            ["코웨이"],
        )

    def test_kofia_normalizes_tranches_and_policy_banks(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <root><message><proframeHeader><pfmResponseDtal/></proframeHeader>
        <BISComDspDatDTO><val1>한국투자증권33-1</val1><val3>20260729</val3>
        <val6>1500</val6></BISComDspDatDTO>
        <BISComDspDatDTO><val1>한국투자증권33-2</val1><val3>20260729</val3>
        <val6>1500</val6></BISComDspDatDTO>
        <BISComDspDatDTO><val1>한국수출입금융 2607타-이표-2</val1>
        <val3>20260729</val3><val6>2500</val6></BISComDspDatDTO>
        </message></root>""".encode("utf-8")

        records = main.parse_kofia_issuance_response(xml)
        categories, _ = main.aggregate_kofia_issuance(records)

        self.assertEqual(categories["회사채"][0]["issuer"], "한국투자증권")
        self.assertEqual(categories["회사채"][0]["amount_eok"], 3000)
        self.assertEqual(categories["은행채"], [])
        self.assertEqual(
            main.normalize_kofia_issuer("한국수출입금융 2607타-이표-2"),
            "수출입은행",
        )
        self.assertEqual(
            main.classify_kofia_bond("한국수출입금융 2607타-이표-2"),
            "은행채",
        )

    def test_kofia_maps_land_housing_bond_to_public_corporation(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <root><message><proframeHeader><pfmResponseDtal/></proframeHeader>
        <BISComDspDatDTO><val1>토지주택채권625</val1><val3>20260811</val3>
        <val6>1000</val6><val9>4.83</val9></BISComDspDatDTO>
        </message></root>""".encode("utf-8")

        records = main.parse_kofia_issuance_response(xml)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["issuer"], "한국토지주택공사")
        self.assertEqual(records[0]["category"], "공사채")

    def test_parses_nh_syndication_schedule_rows(self):
        pdf_text = """
하나에프앤아이  A+  1.5  300  1,500  3,000  NH/KB/한투/신한  개별 -30~+30  7/27(월)  8/4(화)
                          2  700
                          3  500
메리츠금융지주  AA0  2  800  1,500  2,800  NH/KB/한투/신한  개별 -30~+30  7/29(수)  8/6(목)
                          3  700
교보생명보험(신종)  AA0  30NC5  금액 미정  4,000  NH/신한/한투  고정  미정  8/31(월)
  우리금융에프앤아이                  A0        1.5                                         2,500       NH/삼성/신한/키움                                                   개별   -30~+30      9/1(화)     9/9(수)         /
                                        2        1,500                                                                                                       개별   -30~+30                                /
                                        3                                                                                                                    개별   -30~+30                                /
"""
        events = main.parse_nh_syndication_text(
            pdf_text,
            reference_date=date(2026, 7, 24),
            pdf_url="https://example.com/nh.pdf",
        )

        self.assertEqual(len(events), 4)
        hana, meritz, kyobo, woori = events
        self.assertEqual(hana["issuer"], "하나에프앤아이")
        self.assertEqual(hana["term"], "1.5/2/3년")
        self.assertEqual(hana["amount_eok"], 1500)
        self.assertEqual(hana["max_amount_eok"], 3000)
        self.assertEqual(hana["demand_date"], date(2026, 7, 27))
        self.assertEqual(hana["managers"], ["NH", "KB", "한투", "신한"])
        self.assertEqual(
            hana["tranches"],
            [
                {"term": "1.5", "amount_eok": 300},
                {"term": "2", "amount_eok": 700},
                {"term": "3", "amount_eok": 500},
            ],
        )
        self.assertEqual(meritz["issuer"], "메리츠금융지주")
        self.assertEqual(meritz["rating"], "AA0")
        self.assertEqual(meritz["amount_eok"], 1500)
        self.assertEqual(meritz["max_amount_eok"], 2800)
        self.assertIsNone(kyobo["amount_eok"])
        self.assertEqual(kyobo["max_amount_eok"], 4000)
        self.assertIsNone(kyobo["demand_date"])
        self.assertEqual(kyobo["payment_date"], date(2026, 8, 31))
        self.assertEqual(kyobo["rate_band"], "고정")
        self.assertEqual(woori["term"], "1.5/2/3년")
        self.assertEqual(woori["amount_eok"], 1500)
        self.assertEqual(woori["max_amount_eok"], 2500)
        self.assertEqual(
            main.format_tranche_amounts(woori),
            "1.5/2/3년 1,500억원 (최대 2,500억원)",
        )

    @patch.dict(
        "os.environ",
        {"NH_PDF_PLANNED_LOOKAHEAD_DAYS": "45"},
        clear=False,
    )
    @patch("main.extract_nh_syndication_pdf")
    def test_nh_schedule_uses_planned_range_for_dated_events(self, extract_pdf):
        extract_pdf.return_value = (
            """
롯데건설  A0  1  500  1,000  NH/KB  개별 -30~+30  8/19(수)  8/26(수)
삼양패키징  A-  2  600  1,000  NH/KB  개별 -30~+30  8/28(금)  9/4(금)
우리금융에프앤아이  A0  2  1,500  2,500  NH/삼성  개별 -30~+30  9/1(화)  9/9(수)
코웨이  AA-  2  2,000  4,000  NH/KB  개별 -30~+30  9/2(수)  9/10(목)
범위밖회사  A0  2  500  1,000  NH/KB  개별 -30~+30  9/25(금)  10/2(금)
""",
            None,
        )
        response = Mock(status_code=200, content=b"%PDF-fake")
        response.raise_for_status.return_value = None
        requester = Mock()
        requester.get.return_value = response

        result = main.fetch_nh_syndication_schedule(
            date(2026, 8, 10),
            requester=requester,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            [event["issuer"] for event in result["items"]],
            ["롯데건설", "삼양패키징", "우리금융에프앤아이", "코웨이"],
        )

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

    def test_section_uses_concise_nh_mail_format(self):
        section = main.build_bond_market_section(
            {
                "enabled": True,
                "reference_date": date(2026, 7, 29),
                "dart": {"status": "empty", "items": []},
                "nh": {
                    "status": "ok",
                    "source_date": date(2026, 7, 29),
                    "items": [{
                        "source": "nh_pdf",
                        "issuer": "메리츠금융지주",
                        "rating": "AA0",
                        "term": "2/3년",
                        "amount_eok": 1500,
                        "max_amount_eok": 2800,
                        "demand_date": date(2026, 7, 29),
                        "payment_date": date(2026, 8, 6),
                        "managers": ["NH", "KB", "한투", "신한"],
                        "tranches": [
                            {"term": "2", "amount_eok": 800},
                            {"term": "3", "amount_eok": 700},
                        ],
                        "rate_band": "개별 -30~+30bp",
                        "report_url": "https://example.com/nh.pdf",
                    }],
                    "pdf_url": "https://example.com/nh.pdf",
                },
                "kofia": {
                    "status": "ok",
                    "items": [{"issuer": "현대캐피탈"}],
                    "pending_items": [{"issuer": "한국철도공사"}],
                    "categories": {
                        "공사채": [],
                        "은행채": [],
                        "여전채": [{"issuer": "현대캐피탈", "amount_eok": 500}],
                        "회사채": [{"issuer": "한국투자증권", "amount_eok": 3000}],
                    },
                    "pending_categories": {
                        "공사채": ["한국철도공사"],
                        "은행채": [],
                        "여전채": [],
                        "회사채": [],
                    },
                },
            }
        )

        self.assertIn("[ 금일 주요 발행 채권 ]", section)
        self.assertIn("지방채/공사채</b>: 한국철도공사", section)
        self.assertIn("여전채</b>: 현대캐피탈", section)
        self.assertIn("회사채</b>: 한국투자증권", section)
        self.assertIn("메리츠금융지주", section)
        self.assertIn("[ 금일 주요 일정 ]", section)
        self.assertIn("최대 2,800억원", section)
        self.assertIn("대표주관: NH / KB / 한투 / 신한", section)
        self.assertIn("밴드: 개별 -30~+30bp", section)
        self.assertNotIn("GP 체크", section)
        self.assertNotIn("확인 발행액", section)

    @patch.dict("os.environ", {}, clear=True)
    def test_section_default_detail_limit_includes_more_than_four_events(self):
        events = [
            {
                "source": "nh_pdf",
                "issuer": f"예정회사{index}",
                "rating": "A0",
                "term": "2년",
                "amount_eok": 500,
                "max_amount_eok": 1000,
                "demand_date": date(2026, 8, 10 + index),
                "payment_date": date(2026, 8, 17 + index),
                "managers": ["NH"],
                "tranches": [{"term": "2", "amount_eok": 500}],
                "rate_band": "개별 -30~+30bp",
                "report_url": "https://example.com/nh.pdf",
            }
            for index in range(1, 7)
        ]
        section = main.build_bond_market_section({
            "enabled": True,
            "reference_date": date(2026, 8, 10),
            "dart": {"status": "empty", "items": []},
            "kofia": {
                "status": "empty",
                "items": [],
                "pending_items": [],
                "categories": {},
                "pending_categories": {},
            },
            "nh": {
                "status": "ok",
                "source_date": date(2026, 8, 10),
                "items": events,
                "pdf_url": "https://example.com/nh.pdf",
            },
        })

        self.assertIn("예정회사1", section)
        self.assertIn("예정회사6", section)

    def test_bond_history_baseline_then_compacts_unchanged_schedules(self):
        day_one = date(2026, 8, 10)
        events = [
            self._schedule_event(
                "롯데건설",
                date(2026, 8, 19),
                date(2026, 8, 26),
            ),
            self._schedule_event(
                "삼양패키징",
                date(2026, 8, 28),
                date(2026, 9, 4),
            ),
        ]
        history = main.build_bond_history_state(
            path="/tmp/bond-history-test.json"
        )
        first_data = self._bond_market_data(day_one, events)
        first_digest = main.prepare_bond_schedule_digest(
            first_data,
            history,
            day_one,
        )
        first_section = main.build_bond_market_section(
            first_data,
            day_one,
            schedule_digest=first_digest,
        )

        self.assertTrue(first_digest["baseline"])
        self.assertEqual(len(first_digest["baseline_events"]), 2)
        self.assertIn("[ 기준 예정 일정 ]", first_section)
        self.assertIn("[기준]", first_section)

        day_two = date(2026, 8, 11)
        second_data = self._bond_market_data(day_two, events)
        second_digest = main.prepare_bond_schedule_digest(
            second_data,
            first_digest["next_history"],
            day_two,
        )
        second_section = main.build_bond_market_section(
            second_data,
            day_two,
            schedule_digest=second_digest,
        )

        self.assertFalse(second_digest["baseline"])
        self.assertEqual(len(second_digest["unchanged_events"]), 2)
        self.assertEqual(second_digest["new_events"], [])
        self.assertIn("[ 기존 일정 유지 ]", second_section)
        self.assertIn("2건: 롯데건설, 삼양패키징", second_section)
        self.assertNotIn("■ [기준]", second_section)

    def test_bond_history_shows_changed_new_and_due_today_schedules(self):
        day_one = date(2026, 8, 10)
        initial_events = [
            self._schedule_event(
                "변경회사",
                date(2026, 8, 12),
                date(2026, 8, 20),
            ),
            self._schedule_event(
                "당일회사",
                date(2026, 8, 11),
                date(2026, 8, 18),
            ),
        ]
        first_digest = main.prepare_bond_schedule_digest(
            self._bond_market_data(day_one, initial_events),
            main.build_bond_history_state(path="/tmp/bond-history-test.json"),
            day_one,
        )

        day_two = date(2026, 8, 11)
        current_events = [
            self._schedule_event(
                "변경회사",
                date(2026, 8, 11),
                date(2026, 8, 20),
                amount_eok=800,
                max_amount_eok=1200,
            ),
            initial_events[1],
            self._schedule_event(
                "신규회사",
                date(2026, 8, 25),
                date(2026, 9, 1),
            ),
        ]
        second_data = self._bond_market_data(day_two, current_events)
        second_digest = main.prepare_bond_schedule_digest(
            second_data,
            first_digest["next_history"],
            day_two,
        )
        section = main.build_bond_market_section(
            second_data,
            day_two,
            schedule_digest=second_digest,
        )

        self.assertEqual(len(second_digest["changed_events"]), 1)
        self.assertEqual(len(second_digest["new_events"]), 1)
        self.assertEqual(len(second_digest["today_events"]), 1)
        self.assertIn("[변경]", section)
        self.assertIn("수요예측 08/12 → 08/11", section)
        self.assertIn("발행액 500억원 → 800억원", section)
        self.assertIn("[신규]", section)
        self.assertIn("[당일]", section)

    @patch.dict(
        "os.environ",
        {"BOND_HISTORY_MISSING_CONFIRMATIONS": "2"},
        clear=False,
    )
    def test_bond_history_alerts_only_after_two_successful_absences(self):
        day_one = date(2026, 8, 10)
        event = self._schedule_event(
            "미확인회사",
            date(2026, 8, 20),
            date(2026, 8, 27),
        )
        first_digest = main.prepare_bond_schedule_digest(
            self._bond_market_data(day_one, [event]),
            main.build_bond_history_state(path="/tmp/bond-history-test.json"),
            day_one,
        )

        day_two = date(2026, 8, 11)
        second_digest = main.prepare_bond_schedule_digest(
            self._bond_market_data(day_two, [], nh_status="empty"),
            first_digest["next_history"],
            day_two,
        )
        self.assertEqual(second_digest["missing_alerts"], [])

        day_three = date(2026, 8, 12)
        third_data = self._bond_market_data(day_three, [], nh_status="empty")
        third_digest = main.prepare_bond_schedule_digest(
            third_data,
            second_digest["next_history"],
            day_three,
        )
        third_section = main.build_bond_market_section(
            third_data,
            day_three,
            schedule_digest=third_digest,
        )

        self.assertEqual(len(third_digest["missing_alerts"]), 1)
        self.assertIn("[ 일정 재확인 필요 ]", third_section)
        self.assertIn("미확인회사: NH 예정표에서 2회 연속 미확인", third_section)

    def test_bond_history_does_not_advance_on_stale_snapshot(self):
        day_one = date(2026, 8, 10)
        event = self._schedule_event(
            "보존회사",
            date(2026, 8, 20),
            date(2026, 8, 27),
        )
        first_digest = main.prepare_bond_schedule_digest(
            self._bond_market_data(day_one, [event]),
            main.build_bond_history_state(path="/tmp/bond-history-test.json"),
            day_one,
        )
        stale_data = self._bond_market_data(
            date(2026, 8, 11),
            [],
            nh_status="stale",
            source_date=day_one,
        )

        stale_digest = main.prepare_bond_schedule_digest(
            stale_data,
            first_digest["next_history"],
            date(2026, 8, 11),
        )

        self.assertFalse(stale_digest["reliable"])
        self.assertIsNone(stale_digest["next_history"])

    def test_bond_history_ignores_temporary_dart_enrichment_loss(self):
        day_one = date(2026, 8, 10)
        nh_event = self._schedule_event(
            "안정회사",
            date(2026, 8, 20),
            date(2026, 8, 27),
        )
        dart_event = {
            **nh_event,
            "source": "dart",
            "security_type": "무보증사채",
            "start_time": "09:00",
            "end_time": "16:00",
            "amount_eok": 600,
            "report_url": "https://dart.example/report",
        }
        first_data = self._bond_market_data(day_one, [nh_event])
        first_data["dart"] = {"status": "ok", "items": [dart_event]}
        first_digest = main.prepare_bond_schedule_digest(
            first_data,
            main.build_bond_history_state(path="/tmp/bond-history-test.json"),
            day_one,
        )

        day_two = date(2026, 8, 11)
        second_data = self._bond_market_data(day_two, [nh_event])
        second_data["dart"] = {"status": "error", "items": []}
        second_digest = main.prepare_bond_schedule_digest(
            second_data,
            first_digest["next_history"],
            day_two,
        )

        self.assertEqual(len(second_digest["unchanged_events"]), 1)
        self.assertEqual(second_digest["changed_events"], [])

    def test_bond_history_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "bond-history.json"
            history = main.build_bond_history_state(
                events={
                    "issuer|regular": {
                        "event": {"issuer": "발행사"},
                        "fingerprint": "fingerprint",
                        "missing_count": 0,
                    }
                },
                path=str(history_path),
                initialized=True,
            )

            self.assertTrue(main.save_bond_history(history))
            with patch.dict(
                "os.environ",
                {"BOND_HISTORY_FILE": str(history_path)},
                clear=False,
            ):
                loaded = main.load_bond_history()

            self.assertTrue(loaded["initialized"])
            self.assertIn("issuer|regular", loaded["events"])

    def test_section_distinguishes_empty_data_from_collection_error(self):
        section = main.build_bond_market_section(
            {
                "enabled": True,
                "reference_date": date(2026, 7, 23),
                "dart": {"status": "empty", "items": []},
                "kofia": {"status": "error", "items": [], "categories": {}},
            }
        )

        self.assertIn("[ 금일 주요 일정 ]", section)
        self.assertIn("- 없음", section)
        self.assertIn("금투협 발행정보 수집 실패", section)
        self.assertNotIn("GP 체크", section)

    @patch.dict(
        "os.environ",
        {
            "BOND_POLL_ENABLED": "true",
            "BOND_POLL_INTERVAL_SECONDS": "300",
            "BOND_POLL_DEADLINE": "09:00",
        },
        clear=False,
    )
    def test_bond_sources_poll_every_five_minutes_until_ready(self):
        class FakeClock:
            def __init__(self):
                self.current = datetime(2026, 7, 29, 8, 10)
                self.sleeps = []

            def now(self):
                return self.current

            def sleep(self, seconds):
                self.sleeps.append(seconds)
                self.current += timedelta(seconds=seconds)

        clock = FakeClock()
        dart_fetcher = Mock(return_value={"status": "ok", "items": []})
        kofia_fetcher = Mock(side_effect=[
            {"status": "empty", "items": [], "pending_items": []},
            {"status": "empty", "items": [], "pending_items": []},
            {"status": "ok", "items": [{"issuer": "현대캐피탈"}], "pending_items": []},
        ])
        nh_fetcher = Mock(side_effect=[
            {"status": "unavailable", "items": [], "source_date": None},
            {"status": "ok", "items": [], "source_date": date(2026, 7, 29)},
            {"status": "ok", "items": [], "source_date": date(2026, 7, 29)},
        ])

        result = main.fetch_bond_market_data(
            date(2026, 7, 29),
            now_provider=clock.now,
            sleeper=clock.sleep,
            dart_fetcher=dart_fetcher,
            kofia_fetcher=kofia_fetcher,
            nh_fetcher=nh_fetcher,
        )

        self.assertEqual(result["poll_attempts"], 3)
        self.assertEqual(clock.sleeps, [300, 300])
        self.assertEqual(result["fetched_at"], datetime(2026, 7, 29, 8, 20))
        dart_fetcher.assert_called_once_with(date(2026, 7, 29))

    @patch.dict(
        "os.environ",
        {
            "BOND_POLL_ENABLED": "true",
            "BOND_POLL_INTERVAL_SECONDS": "300",
            "BOND_POLL_DEADLINE": "09:00",
        },
        clear=False,
    )
    def test_bond_poll_stops_at_nine(self):
        class FakeClock:
            def __init__(self):
                self.current = datetime(2026, 7, 29, 8, 55)

            def now(self):
                return self.current

            def sleep(self, seconds):
                self.current += timedelta(seconds=seconds)

        clock = FakeClock()
        result = main.fetch_bond_market_data(
            date(2026, 7, 29),
            now_provider=clock.now,
            sleeper=clock.sleep,
            dart_fetcher=Mock(return_value={"status": "empty", "items": []}),
            kofia_fetcher=Mock(
                return_value={"status": "empty", "items": [], "pending_items": []}
            ),
            nh_fetcher=Mock(
                return_value={"status": "unavailable", "items": [], "source_date": None}
            ),
        )

        self.assertEqual(result["poll_attempts"], 2)
        self.assertTrue(result["deadline_reached"])
        self.assertEqual(result["fetched_at"], datetime(2026, 7, 29, 9, 0))


if __name__ == "__main__":
    unittest.main()
