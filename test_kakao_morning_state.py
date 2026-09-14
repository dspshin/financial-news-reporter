import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from kakao_morning_state import KST, begin, finish, receipt_path


class KakaoDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / "2026-09-14-am"
        self.folder.mkdir()
        (self.folder / "briefing.png").write_bytes(b"reviewed image")
        self.state = self.root / "state"
        self.now = datetime(2026, 9, 14, 8, 30, tzinfo=KST)
        self.root_patch = patch("kakao_morning_state.ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        telegram = self.root / ".morning_image_delivery"
        telegram.mkdir()
        self.telegram_receipt = telegram / f"{self.folder.name}-destination.json"
        self.telegram_record = {
            "status": "sent", "edition": self.folder.name, "message_id": 123,
            "image_sha256": hashlib.sha256(b"reviewed image").hexdigest(),
        }
        self.telegram_receipt.write_text(json.dumps(self.telegram_record))

    @patch("kakao_morning_state.validate_bundle")
    def test_telegram_must_confirm_the_same_image_first(self, validate):
        for change in ({"status": "uncertain"}, {"image_sha256": "different"}, {"message_id": None}):
            with self.subTest(change=change):
                self.telegram_receipt.write_text(json.dumps(self.telegram_record | change))
                with self.assertRaises(ValueError):
                    begin(self.folder, "target", "target", self.state, self.now)
                self.assertFalse(self.state.exists())
        self.telegram_receipt.unlink()
        with self.assertRaises(ValueError):
            begin(self.folder, "target", "target", self.state, self.now)

    @patch("kakao_morning_state.validate_bundle")
    def test_wrong_room_is_blocked_before_creating_receipt(self, validate):
        with self.assertRaises(ValueError):
            begin(self.folder, "target", "other", self.state, self.now)
        self.assertFalse(self.state.exists())
        validate.assert_not_called()

    @patch("kakao_morning_state.validate_bundle")
    def test_sent_receipt_blocks_second_attempt(self, validate):
        begin(self.folder, "target", "target", self.state, self.now)
        finish(self.folder, "target", self.state, "sent", "Outgoing image visible", self.now)
        with self.assertRaises(ValueError):
            begin(self.folder, "target", "target", self.state, self.now)
        stored = json.loads(receipt_path(self.state, self.folder, "target").read_text())
        self.assertEqual(stored["status"], "sent")

    @patch("kakao_morning_state.validate_bundle")
    def test_uncertain_and_changed_image_cannot_be_marked_sent(self, validate):
        begin(self.folder, "target", "target", self.state, self.now)
        (self.folder / "briefing.png").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            finish(self.folder, "target", self.state, "sent", "Image visible", self.now)
        (self.folder / "briefing.png").write_bytes(b"reviewed image")
        finish(self.folder, "target", self.state, "uncertain", "Connection lost", self.now)
        with self.assertRaises(ValueError):
            begin(self.folder, "target", "target", self.state, self.now)


if __name__ == "__main__":
    unittest.main()
