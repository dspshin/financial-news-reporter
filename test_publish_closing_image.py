import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import publish_closing_image as publisher
import publish_morning_image as shared


class ClosingPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "2026-10-12-pm"
        self.folder.mkdir()
        header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (1254).to_bytes(4, "big") * 2
        (self.folder / "briefing.png").write_bytes(header)
        for name in publisher.ARTIFACTS:
            if name != "briefing.png":
                (self.folder / name).write_text("verified", encoding="utf-8")
        (self.folder / "caption.txt").write_text("2026-10-12 마감 시황 · 16:20 KST", encoding="utf-8")
        self.qa = {
            "status": "passed", "date": "2026-10-12", "cutoff": "2026-10-12T16:20:00+09:00",
            "regular_session_closed_at": "2026-10-12T15:30:00+09:00",
            "qa_at_kst": "2026-10-12T16:40:00+09:00",
            "market_day": {"date": "2026-10-12", "status": "open_and_regular_closed",
                           "checked_at": "2026-10-12T16:21:00+09:00",
                           "sources": ["https://data.krx.co.kr/"]},
            "checks": {"visual_match": True},
        }
        self.now = datetime(2026, 10, 12, 17, 0, tzinfo=publisher.KST)
        self.save_qa()

    def save_qa(self):
        self.qa["sha256"] = {n: publisher.sha256(self.folder / n) for n in publisher.ARTIFACTS}
        (self.folder / "qa.json").write_text(json.dumps(self.qa), encoding="utf-8")

    def invoke(self, *args):
        with patch.object(publisher, "ROOT", self.root), \
                patch.object(publisher, "datetime") as clock, \
                patch("sys.argv", ["publisher", "--bundle", str(self.folder), *args]), \
                contextlib.redirect_stdout(io.StringIO()):
            clock.now.return_value = self.now
            clock.fromisoformat.side_effect = datetime.fromisoformat
            publisher.main()

    def test_close_time_cutoff_and_edition_guards(self):
        self.assertIn("16:20", publisher.validate_bundle(self.folder, self.now))
        for now in (self.now.replace(day=13), self.now.replace(hour=16, minute=19)):
            with self.assertRaises(ValueError):
                publisher.validate_bundle(self.folder, now)
        with self.assertRaises(ValueError):
            publisher.validate_bundle(self.folder.with_name("2026-10-12-am"), self.now)
        self.qa["cutoff"] = "2026-10-12T07:35:00+09:00"
        self.save_qa()
        with self.assertRaisesRegex(ValueError, "cutoff"):
            publisher.validate_bundle(self.folder, self.now)

    def test_missing_close_or_unofficial_opening_cannot_publish(self):
        for key, value in (("status", "open"), ("sources", ["https://example.com/"])):
            with self.subTest(key=key):
                old = self.qa["market_day"][key]
                self.qa["market_day"][key] = value
                self.save_qa()
                with self.assertRaises(ValueError):
                    publisher.validate_bundle(self.folder, self.now)
                self.qa["market_day"][key] = old
        self.qa["regular_session_closed_at"] = "2026-10-12T17:00:00+09:00"
        self.save_qa()
        with self.assertRaisesRegex(ValueError, "Regular close"):
            publisher.validate_bundle(self.folder, self.now)

    def test_failed_visual_qa_and_changed_files_block_send(self):
        self.qa["checks"]["visual_match"] = False
        self.save_qa()
        with self.assertRaisesRegex(ValueError, "Every required"):
            publisher.validate_bundle(self.folder, self.now)
        self.qa["checks"]["visual_match"] = True
        self.save_qa()
        (self.folder / "caption.txt").write_text("changed")
        with self.assertRaisesRegex(ValueError, "changed after review"):
            publisher.validate_bundle(self.folder, self.now)

    def test_missing_item_placeholder_blocks_review(self):
        (self.folder / "manuscript.txt").write_text("거래대금 미확인")
        self.save_qa()
        with self.assertRaisesRegex(ValueError, "omitted"):
            publisher.validate_bundle(self.folder, self.now)

    @patch.object(publisher, "credentials")
    @patch.object(shared, "api")
    def test_dry_run_and_unsent_status_do_not_use_network(self, api, credentials):
        self.invoke("--dry-run")
        with self.assertRaises(SystemExit) as result:
            self.invoke("--status")
        self.assertEqual(result.exception.code, 2)
        credentials.assert_not_called()
        api.assert_not_called()

    @patch.object(shared, "api", return_value={"message_id": 45, "photo": [{}]})
    @patch.object(publisher, "check_channel", return_value={"id": -1234})
    @patch.object(publisher, "credentials", return_value=("test", "channel"))
    def test_pm_send_uses_am_transport_and_cannot_duplicate(self, credentials, check, api):
        self.invoke()
        self.invoke()
        self.invoke("--status")
        api.assert_called_once()
        self.assertEqual(api.call_args.args[1], "sendPhoto")
        self.assertFalse((self.root / ".morning_image_delivery").exists())
        record = publisher.delivery_status(self.folder, self.root / publisher.STATE_NAME)
        self.assertEqual(record["status"], "sent")
        self.assertEqual(record["message_id"], 45)

    @patch.object(shared, "api", side_effect=RuntimeError("response uncertain"))
    @patch.object(publisher, "check_channel", return_value={"id": -1234})
    @patch.object(publisher, "credentials", return_value=("test", "channel"))
    def test_uncertain_receipt_is_preserved_and_blocks_retry(self, credentials, check, api):
        with self.assertRaises(RuntimeError):
            self.invoke()
        with self.assertRaisesRegex(RuntimeError, "do not resend"):
            self.invoke()
        self.assertEqual(api.call_count, 1)
        self.assertEqual(publisher.delivery_status(self.folder, self.root / publisher.STATE_NAME)["status"], "uncertain")

    def test_damaged_record_never_becomes_a_new_send(self):
        state = self.root / publisher.STATE_NAME
        state.mkdir()
        (state / f"{self.folder.name}-test.json").write_text("broken")
        with self.assertRaisesRegex(RuntimeError, "Damaged"):
            publisher.delivery_status(self.folder, state)


if __name__ == "__main__":
    unittest.main()
