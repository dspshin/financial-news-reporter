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
        (self.folder / "caption.txt").write_text("2026-09-15 아침 시황 · 08:00 KST", encoding="utf-8")
        self.qa = {"status": "passed", "date": "2026-09-15", "cutoff": "2026-09-15T08:00:00+09:00",
                   "sha256": {p.name: publisher.sha256(p) for p in self.folder.iterdir()}}
        (self.folder / "qa.json").write_text(json.dumps(self.qa), encoding="utf-8")
        self.now = datetime(2026, 9, 15, 8, 20, tzinfo=publisher.KST)

    def test_valid_bundle_and_reject_changed_image(self):
        self.assertIn("08:00", publisher.validate_bundle(self.folder, self.now))
        with (self.folder / "briefing.png").open("ab") as handle:
            handle.write(b"changed")
        with self.assertRaisesRegex(ValueError, "changed after review"):
            publisher.validate_bundle(self.folder, self.now)

    def test_reject_stale_edition_and_early_run(self):
        with self.assertRaisesRegex(ValueError, "today"):
            publisher.validate_bundle(self.folder, self.now.replace(day=16))
        with self.assertRaisesRegex(ValueError, "08:00"):
            publisher.validate_bundle(self.folder, self.now.replace(hour=7))

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


if __name__ == "__main__":
    unittest.main()
