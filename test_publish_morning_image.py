import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import publish_morning_image as publisher


class MorningPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "2026-09-15-am"
        self.folder.mkdir()
        # Header-only fixture: validation tests do not render images.
        header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (1254).to_bytes(4, "big") * 2
        (self.folder / "briefing.png").write_bytes(header)
        for name in ("manuscript.txt", "sources.md"):
            (self.folder / name).write_text("reviewed", encoding="utf-8")
        (self.folder / "caption.txt").write_text("2026-09-15 아침 시황 · 07:40 KST", encoding="utf-8")
        self.qa = {"status": "passed", "date": "2026-09-15", "cutoff": "2026-09-15T07:40:00+09:00",
                   "market_day": {"date": "2026-09-15", "status": "open",
                                  "checked_at": "2026-09-15T07:40:00+09:00",
                                  "sources": ["https://open.krx.co.kr/"]},
                   "sha256": {p.name: publisher.sha256(p) for p in self.folder.iterdir()}}
        (self.folder / "qa.json").write_text(json.dumps(self.qa), encoding="utf-8")
        self.now = datetime(2026, 9, 15, 7, 50, tzinfo=publisher.KST)

    def test_valid_bundle_and_reject_changed_image(self):
        self.assertIn("07:40", publisher.validate_bundle(self.folder, self.now))
        with (self.folder / "briefing.png").open("ab") as handle:
            handle.write(b"changed")
        with self.assertRaisesRegex(ValueError, "changed after review"):
            publisher.validate_bundle(self.folder, self.now)

    def test_reject_stale_edition_and_early_run(self):
        with self.assertRaisesRegex(ValueError, "today"):
            publisher.validate_bundle(self.folder, self.now.replace(day=16))
        with self.assertRaisesRegex(ValueError, "07:40"):
            publisher.validate_bundle(self.folder, self.now.replace(hour=7, minute=39, second=59))

    def test_missing_market_day_check_blocks_publishing(self):
        del self.qa["market_day"]
        (self.folder / "qa.json").write_text(json.dumps(self.qa), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "market opening"):
            publisher.validate_bundle(self.folder, self.now)

    def test_new_cutoff_allows_0740_and_rejects_old_review_and_caption(self):
        self.assertIn("07:40", publisher.validate_bundle(self.folder, self.now.replace(minute=40)))
        self.qa["cutoff"] = "2026-09-15T08:00:00+09:00"
        (self.folder / "qa.json").write_text(json.dumps(self.qa), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "cutoff"):
            publisher.validate_bundle(self.folder, self.now)
        self.qa["cutoff"] = "2026-09-15T07:40:00+09:00"
        (self.folder / "caption.txt").write_text("2026-09-15 아침 시황 · 08:00 KST", encoding="utf-8")
        self.qa["sha256"]["caption.txt"] = publisher.sha256(self.folder / "caption.txt")
        (self.folder / "qa.json").write_text(json.dumps(self.qa), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Caption"):
            publisher.validate_bundle(self.folder, self.now)

    @patch.object(publisher, "api", return_value={"message_id": 123, "photo": [{}]})
    def test_success_has_receipt_and_prevents_duplicate(self, api):
        args = (self.folder, "test-token", -1234, "caption", self.root / "receipts")
        self.assertEqual("sent", publisher.publish(*args)["status"])
        self.assertEqual("already_sent", publisher.publish(*args)["status"])
        self.assertEqual(1, api.call_count)

    @patch.object(publisher, "api", side_effect=RuntimeError("response uncertain"))
    def test_uncertain_delivery_cannot_resend(self, api):
        args = (self.folder, "test-token", -1234, "caption", self.root / "receipts")
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            publisher.publish(*args)
        with self.assertRaisesRegex(RuntimeError, "do not resend"):
            publisher.publish(*args)
        self.assertEqual(1, api.call_count)
        receipt = next((self.root / "receipts").glob("*.json"))
        self.assertEqual("uncertain", json.loads(receipt.read_text())["status"])

    @patch.object(publisher.requests, "post", side_effect=publisher.requests.Timeout("secret-token in URL"))
    def test_network_error_does_not_leak_credentials(self, post):
        with self.assertRaises(RuntimeError) as error:
            publisher.api("secret-token", "getMe")
        self.assertNotIn("secret-token", str(error.exception))

    @patch.object(publisher, "api", return_value={"message_id": 123, "photo": [{}]})
    def test_pending_checkpoint_is_written_before_send(self, api):
        def on_pending():
            record = json.loads(next((self.root / "receipts").glob("*.json")).read_text())
            self.assertEqual(record["status"], "pending")
            api.assert_not_called()
        result = publisher.publish(self.folder, "test", -1234, "caption",
                                   self.root / "receipts", on_pending=on_pending)
        self.assertEqual(result["status"], "sent")

    @patch.object(publisher, "api")
    def test_pending_checkpoint_failure_does_not_send(self, api):
        def on_pending():
            raise OSError("state disk unavailable")
        args = (self.folder, "test", -1234, "caption", self.root / "receipts")
        with self.assertRaises(OSError):
            publisher.publish(*args, on_pending=on_pending)
        with self.assertRaises(RuntimeError):
            publisher.publish(*args)
        api.assert_not_called()

    @patch.object(publisher, "api", return_value={"message_id": 123, "photo": [{}]})
    def test_cli_syncs_pending_and_sent_even_if_final_summary_write_fails(self, api):
        observed = []
        def sync(folder, root):
            record = json.loads(next((root / ".morning_image_delivery").glob("*.json")).read_text())
            observed.append(record["status"])
            if record["status"] == "sent":
                raise OSError("summary write failed")
        with patch.object(publisher, "ROOT", self.root), \
                patch.object(publisher, "validate_bundle", return_value="caption"), \
                patch.object(publisher, "credentials", return_value=("test", "channel")), \
                patch.object(publisher, "check_channel", return_value={"id": -1234}), \
                patch("morning_delivery_status.reconcile", side_effect=sync), \
                patch("sys.argv", ["publisher", "--bundle", str(self.folder)]), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(OSError):
                publisher.main()
        self.assertEqual(observed, ["pending", "sent"])
        record = json.loads(next((self.root / ".morning_image_delivery").glob("*.json")).read_text())
        self.assertEqual(record["status"], "sent")
        self.assertEqual(api.call_count, 1)


if __name__ == "__main__":
    unittest.main()
