import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

import morning_delivery_status as delivery
import wordpress_briefing as blog
from kakao_morning_state import receipt_path
from image_delivery_edition import kakao_state_name, telegram_state_name


class WordPressDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = {"site_id": "123", "site_url": "https://example.wordpress.com",
                       "effective_from": "2026-11-09-am", "category_ids": {"am": 41, "pm": 42}}
        (self.root / "WORDPRESS_DELIVERY.json").write_text(json.dumps(self.config))
        (self.root / ".kakao_morning.json").write_text(json.dumps({
            "room_names": ["first", "last"],
            "direct_delivery": {"effective_from": "2026-10-07", "room_names": ["first", "last"],
                                "max_retries": 3, "continue_on_room_failure": True}}))
        self.make_bundle("am")

    def make_bundle(self, edition):
        self.folder = self.root / ("2026-11-09-" + edition)
        self.folder.mkdir(exist_ok=True)
        self.now = datetime.fromisoformat("2026-11-09T" + ("08:30" if edition == "am" else "17:00") + ":00+09:00")
        cutoff = "07:35" if edition == "am" else "16:20"
        Image.new("RGB", (1254, 1254), "white").save(self.folder / "briefing.png")
        (self.folder / "manuscript.txt").write_text("11/9 시황\n핵심 변화\n\n① 증시\n검증된 숫자 1,234.56 −1.00%\n")
        (self.folder / "sources.md").write_text("- [공식 출처](https://example.com/source) 07:30 관측\n")
        (self.folder / "source-checks.json").write_text('{"verified":true}')
        (self.folder / "qa.json").write_text(json.dumps({"status": "passed", "date": "2026-11-09",
                                "cutoff": "2026-11-09T" + cutoff + ":00+09:00"}))
        (self.folder / "blog-analysis.md").write_text("### 선정 근거\n\n당시 확인한 근거와 조건. [원문](https://example.com/source)")
        self.review()
        telegram = self.root / telegram_state_name(self.folder) / (self.folder.name + "-channel.json")
        telegram.parent.mkdir(exist_ok=True)
        telegram.write_text(json.dumps({"edition": self.folder.name, "status": "sent", "message_id": 10,
                                      "image_sha256": blog.sha256(self.folder / "briefing.png")}))
        for room in ("first", "last"):
            path = receipt_path(self.root / kakao_state_name(self.folder), self.folder, room)
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps({"edition": self.folder.name, "room": room, "status": "sent",
                                      "image_sha256": blog.sha256(self.folder / "briefing.png"),
                                      "evidence": "Observed matching outgoing image",
                                      "observed_at_kst": self.now.isoformat()}))

    def review(self):
        (self.folder / "blog-qa.json").write_text(json.dumps({"status": "passed", "edition": self.folder.name,
            "checks": {"sources_and_analysis_match": True},
            "checked_at_kst": self.now.isoformat(), "sha256": {
                name: blog.sha256(self.folder / name) for name in blog.INPUTS if name != "blog-qa.json"}}))

    def act(self, action, **kwargs):
        with patch.object(blog, "validate_bundle"):
            if action == "published":
                payload = json.loads((self.folder / "wordpress-post.json").read_text())
                response = Mock(content=payload["content"].encode())
                response.raise_for_status.return_value = None
                with patch.object(blog.requests, "get", return_value=response):
                    return blog.perform(action, self.folder, self.root, self.now, **kwargs)
            return blog.perform(action, self.folder, self.root, self.now, **kwargs)

    def plan(self):
        return delivery.reconcile(self.folder, self.root, self.now)

    def upload(self):
        self.act("begin-media")
        response = Mock(content=(self.folder / "briefing.png").read_bytes())
        response.raise_for_status.return_value = None
        data = {"data": {"id": 21, "mime_type": "image/png",
                          "source_url": "https://example.wordpress.com/wp-content/uploads/briefing.png"}}
        with patch.object(blog.requests, "get", return_value=response):
            return self.act("media", result=data)

    def post_readback(self):
        payload = json.loads((self.folder / "wordpress-post.json").read_text())
        return {"data": payload | {"id": 31, "link": self.config["site_url"] + "/" + payload["slug"] + "/"}}

    def test_both_editions_require_wordpress_after_final_kakao_and_finish(self):
        for edition in ("am", "pm"):
            if edition == "pm":
                self.make_bundle(edition)
            self.assertEqual((self.plan()["next_target"], self.plan()["next_action"]),
                             ("wordpress", "prepare_wordpress"))
            self.assertEqual(self.plan()["status"], "incomplete")
            self.act("prepare")
            self.upload()
            payload = json.loads((self.folder / "wordpress-post.json").read_text())
            self.assertEqual(payload["categories"], [self.config["category_ids"][edition]])
            self.act("begin-post")
            self.act("published", result=self.post_readback())
            state = self.plan()
            self.assertEqual(state["status"], "completed")
            self.assertTrue(state["all_targets_handled"])
            self.assertEqual(state["deliveries"]["wordpress"]["post_id"], 31)
            with self.assertRaises(ValueError):
                self.act("prepare")

    def test_unprocessed_last_room_blocks_wordpress(self):
        path = receipt_path(self.root / kakao_state_name(self.folder), self.folder, "last")
        path.unlink()
        self.assertEqual(self.plan()["next_target"], "last")
        with self.assertRaisesRegex(ValueError, "every Kakao"):
            self.act("prepare")
        self.assertFalse(blog.receipt_path(self.folder, self.root).exists())

    def test_uncertain_mutation_is_never_reissued_and_can_be_resolved(self):
        self.act("prepare")
        self.upload()
        self.act("begin-post")
        with self.assertRaises(ValueError):
            self.act("begin-post")
        self.act("defer", evidence="Connector timed out after post request; inspect exact slug")
        original = blog.receipt_path(self.folder, self.root).read_bytes()
        self.act("defer", evidence="Same blocking condition on resume")
        self.assertEqual(blog.receipt_path(self.folder, self.root).read_bytes(), original)
        self.assertEqual(self.plan()["status"], "incomplete")
        self.assertTrue(self.plan()["all_targets_handled"])
        self.act("published", result=self.post_readback())
        self.assertEqual(self.plan()["status"], "completed")

    def test_uploaded_wrong_image_keeps_request_journal(self):
        self.act("prepare")
        self.act("begin-media")
        response = Mock(content=b"not an image")
        response.raise_for_status.return_value = None
        with patch.object(blog.requests, "get", return_value=response), self.assertRaises(OSError):
            self.act("media", result={"id": 21, "mime_type": "image/png",
                "source_url": "https://example.wordpress.com/wp-content/uploads/wrong.png"})
        self.assertEqual(self.plan()["next_action"], "inspect_wordpress_media_no_reupload")
        with self.assertRaises(ValueError):
            self.act("begin-media")

    def test_changed_input_or_payload_cannot_authorize_publication(self):
        self.act("prepare")
        self.upload()
        (self.folder / "wordpress-post.json").write_text("{}")
        self.assertEqual(self.plan()["deliveries"]["wordpress"]["status"], "invalid")
        with self.assertRaises(ValueError):
            self.act("begin-post")

    def test_readback_with_wrong_numbers_or_email_enabled_stays_pending(self):
        self.act("prepare")
        self.upload()
        self.act("begin-post")
        result = self.post_readback()
        result["data"]["content"] = result["data"]["content"].replace("1,234.56", "9,999.99")
        with self.assertRaises(ValueError):
            self.act("published", result=result)
        result = self.post_readback()
        result["data"]["meta"]["_jetpack_dont_email_post_to_subs"] = False
        with self.assertRaises(ValueError):
            self.act("published", result=result)
        self.assertEqual(self.plan()["next_action"], "inspect_wordpress_post_no_repost")

    def test_bad_or_missing_review_is_blocked(self):
        (self.folder / "blog-analysis.md").write_text("changed after review")
        with self.assertRaisesRegex(ValueError, "editorial input"):
            self.act("prepare")

    def test_missing_or_wrong_category_cannot_be_published(self):
        self.act("prepare")
        self.upload()
        self.act("begin-post")
        for categories in (None, [1], [42], [41, 1]):
            result = self.post_readback()
            result["data"]["categories"] = categories
            with self.assertRaisesRegex(ValueError, "category"):
                self.act("published", result=result)
        self.assertEqual(self.plan()["next_action"], "inspect_wordpress_post_no_repost")

    def test_unconfigured_category_blocks_preparation_without_upload(self):
        for categories in ({}, {"am": True}, {"am": 0}, {"am": "41"}, []):
            self.config["category_ids"] = categories
            (self.root / "WORDPRESS_DELIVERY.json").write_text(json.dumps(self.config))
            with self.assertRaisesRegex(ValueError, "category"):
                self.act("prepare")
            self.assertFalse(blog.receipt_path(self.folder, self.root).exists())

    def test_prepare_does_not_replace_incomplete_summary_with_channel_only_success(self):
        state = self.plan()
        original = (self.folder / "run-status.json").read_bytes()
        self.assertEqual(state["status"], "incomplete")
        self.act("prepare")
        self.assertEqual((self.folder / "run-status.json").read_bytes(), original)

    def test_parallel_preparation_has_only_one_winner(self):
        def attempt():
            try:
                return self.act("prepare")["status"]
            except ValueError:
                return "blocked"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(results, ["prepared", "blocked"])

    def test_existing_completed_edition_is_not_reopened(self):
        self.config["effective_from"] = "2026-11-09-pm"
        (self.root / "WORDPRESS_DELIVERY.json").write_text(json.dumps(self.config))
        self.assertEqual(self.plan()["status"], "completed")
        self.assertNotIn("wordpress", self.plan()["deliveries"])

    def test_coming_soon_page_cannot_be_marked_public(self):
        self.act("prepare")
        self.upload()
        self.act("begin-post")
        response = Mock(content=b"<html><p>Coming Soon</p></html>")
        response.raise_for_status.return_value = None
        with patch.object(blog.requests, "get", return_value=response), self.assertRaisesRegex(ValueError, "Public WordPress"):
            blog.perform("published", self.folder, self.root, self.now, result=self.post_readback())
        self.assertEqual(self.plan()["next_action"], "inspect_wordpress_post_no_repost")

    def test_damaged_receipts_are_preserved_for_inspection(self):
        self.act("prepare")
        path = blog.receipt_path(self.folder, self.root)
        for damaged in ("{broken", "[]", '{"input_sha256":[]}'):
            path.write_text(damaged)
            self.assertEqual(self.plan()["next_action"], "inspect_wordpress_receipt_no_repost")
            with self.assertRaises(ValueError):
                self.act("begin-media")
            self.assertEqual(path.read_text(), damaged)

    def test_read_retry_limit_survives_deferral_and_resume(self):
        self.act("prepare")
        self.act("begin-media")
        self.act("retry", evidence="Upload response lost; read exact media filename")
        self.act("defer", evidence="Media result remains uncertain")
        for _ in range(2):
            self.act("retry", evidence="Read-only inspection on resume")
        with self.assertRaisesRegex(ValueError, "retry limit"):
            self.act("retry", evidence="Fourth inspection")
        self.assertEqual(len(blog.delivery_status(self.folder, self.root)["read_retries"]["media_requested"]), 3)


if __name__ == "__main__":
    unittest.main()
