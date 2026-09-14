import unittest
from datetime import date
from types import SimpleNamespace

from read_morning_news import CHANNEL, collect_posts, parse_page


def post(number, stamp, text="뉴스", channel=CHANNEL):
    return f'''<div class="tgme_widget_message" data-post="{channel}/{number}">
    <div class="tgme_widget_message_text">{text}</div>
    <a class="tgme_widget_message_date"><time datetime="{stamp}"></time></a></div>'''


class MorningNewsTests(unittest.TestCase):
    def test_korean_date_and_cutoff_use_publication_time(self):
        html = "".join([
            post(1, "2026-09-13T22:31:37+00:00", "오늘 뉴스"),
            post(2, "2026-09-13T22:40:00+00:00", "정각"),
            post(3, "2026-09-13T22:40:01+00:00", "기준 이후"),
            post(4, "2026-09-12T22:30:00+00:00", "9/14 뉴스라는 오래된 본문"),
            post(5, "2026-09-14T07:30:00", "시간대 없음"),
            post(6, "2026-09-13T22:30:00+00:00", channel="other"),
        ])
        posts, _, _ = parse_page(html, date(2026, 9, 14))
        self.assertEqual([p["message_id"] for p in posts], [1, 2])
        self.assertEqual(posts[0]["published_at_kst"], "2026-09-14T07:31:37+09:00")

    def test_keeps_text_and_source_links(self):
        html = post(1, "2026-09-13T22:30:00Z", '<b>지수</b> +1%<br>원문 <a href="https://example.org/news">기사</a>')
        posts, _, _ = parse_page(html, date(2026, 9, 14))
        self.assertEqual(posts[0]["text"], "지수 +1%\n원문 기사")
        self.assertEqual(posts[0]["links"], ["https://example.org/news"])

    def test_pages_back_without_using_after_cutoff_or_stale_posts(self):
        pages = [
            post(3, "2026-09-14T00:01:00Z") + '<a class="tme_messages_more" data-before="3"></a>',
            post(2, "2026-09-13T22:30:00Z") + post(1, "2026-09-12T22:30:00Z"),
        ]
        calls = []

        def get(url, **kwargs):
            calls.append(kwargs["params"])
            return SimpleNamespace(text=pages.pop(0), raise_for_status=lambda: None)

        posts = collect_posts(date(2026, 9, 14), requester=get)
        self.assertEqual([p["message_id"] for p in posts], [2])
        self.assertEqual(calls, [None, {"before": "3"}])


if __name__ == "__main__":
    unittest.main()
