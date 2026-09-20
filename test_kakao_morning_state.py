import contextlib
import io
import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import kakao_morning_state as kakao

from kakao_morning_state import KST, begin, finish, checkpoint, skip, receipt_path, configured_rooms, select_room, require_room_active


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

    @patch("kakao_morning_state.validate_bundle")
    def test_next_room_requires_first_room_success_and_same_image(self, validate):
        first, second = "x삼성 투자방", "금복회 장자풍도 60대下"
        def start_second():
            return begin(self.folder, second, second, self.state, self.now, [first])
        with self.assertRaises(ValueError):
            start_second()
        begin(self.folder, first, first, self.state, self.now)
        with self.assertRaises(ValueError):
            start_second()
        finish(self.folder, first, self.state, "sent", "First outgoing image visible", self.now)
        path = receipt_path(self.state, self.folder, first)
        original = json.loads(path.read_text())
        for change in ({"status": "uncertain"}, {"image_sha256": "different"},
                       {"room": second}, {"edition": "2026-09-11-am"}):
            with self.subTest(change=change):
                path.write_text(json.dumps(original | change))
                with self.assertRaises(ValueError):
                    start_second()
                self.assertFalse(receipt_path(self.state, self.folder, second).exists())
        path.write_text(json.dumps(original))
        record = start_second()
        self.assertEqual(record["previous_kakao_receipts"], [path.name])
        finish(self.folder, second, self.state, "sent", "Second outgoing image visible", self.now)
        with self.assertRaises(ValueError):
            start_second()
        self.assertEqual(json.loads(path.read_text()), original)

    def test_config_and_explicit_room_selection(self):
        self.assertEqual(configured_rooms({"room_name": "legacy"}), ["legacy"])
        rooms = configured_rooms({"room_names": ["first", "second"]})
        self.assertEqual(select_room(rooms, "second"), "second")
        self.assertEqual(select_room(["legacy"], None), "legacy")
        for requested in (None, "unapproved", "second "):
            with self.assertRaises(ValueError):
                select_room(rooms, requested)
        for config in ({}, {"room_names": []}, {"room_names": "first"},
                       {"room_names": ["first", "first"]}, {"room_names": [" first"]},
                       {"room_names": ["first"], "room_name": "other"}):
            with self.assertRaises(ValueError):
                configured_rooms(config)

    @patch("kakao_morning_state.validate_bundle")
    def test_ui_checkpoints_never_reset_or_repeat_send_requested(self, validate):
        begin(self.folder, "target", "target", self.state, self.now)
        for phase in ("file_selected", "attachment_ready", "send_requested"):
            checkpoint(self.folder, "target", "target", self.state, phase, "UI verified", self.now)
        for phase in ("file_selected", "attachment_ready", "send_requested"):
            with self.assertRaises(ValueError):
                checkpoint(self.folder, "target", "target", self.state, phase, "UI verified", self.now)
        with self.assertRaises(ValueError):
            skip(self.folder, "target", self.state, "not_logged_in", "Login screen", self.now)
        finish(self.folder, "target", self.state, "sent", "Outgoing image visible", self.now)

    @patch("kakao_morning_state.validate_bundle")
    def test_skip_requires_allowed_reason_and_cannot_overwrite_sent(self, validate):
        with self.assertRaises(ValueError):
            skip(self.folder, "target", self.state, "timeout", "Stopped", self.now)
        begin(self.folder, "target", "target", self.state, self.now)
        record = skip(self.folder, "target", self.state, "not_logged_in", "Login screen", self.now)
        self.assertEqual(record["status"], "skipped")
        with self.assertRaises(ValueError):
            begin(self.folder, "target", "target", self.state, self.now)
        begin(self.folder, "second", "second", self.state, self.now)
        finish(self.folder, "second", self.state, "sent", "Outgoing image", self.now)
        with self.assertRaises(ValueError):
            skip(self.folder, "second", self.state, "os_locked", "Locked", self.now)

    @patch("kakao_morning_state.validate_bundle")
    def test_checkpoints_reject_wrong_room_changed_image_and_skipped_phases(self, validate):
        begin(self.folder, "target", "target", self.state, self.now)
        for room, phase in (("wrong", "file_selected"), ("target", "send_requested")):
            with self.assertRaises(ValueError):
                checkpoint(self.folder, "target", room, self.state, phase, "Observed", self.now)
        (self.folder / "briefing.png").write_bytes(b"different")
        with self.assertRaises(ValueError):
            checkpoint(self.folder, "target", "target", self.state, "file_selected", "Observed", self.now)

    @patch("kakao_morning_state.validate_bundle")
    def test_cli_automatically_syncs_begin_checkpoint_and_sent(self, validate):
        (self.root / ".kakao_morning.json").write_text(json.dumps({"room_names": ["target"]}))
        base = ["kakao", "--bundle", str(self.folder), "--room", "target"]
        cases = [(["begin", "--observed-room", "target"], "pending", "room_verified"),
                 (["checkpoint", "--observed-room", "target", "--phase", "file_selected", "--evidence", "Selected PNG"], "pending", "file_selected"),
                 (["sent", "--evidence", "Observed outgoing image"], "sent", "file_selected")]
        for args, status, phase in cases:
            with patch("sys.argv", base + args), contextlib.redirect_stdout(io.StringIO()):
                kakao.main()
            summary = json.loads((self.folder / "run-status.json").read_text())
            self.assertEqual(summary["deliveries"]["target"]["status"], status)
            self.assertEqual(summary["deliveries"]["target"]["ui_phase"], phase)

    def test_new_room_cannot_start_before_authorized_date(self):
        config = {"room_names": ["first", "second"],
                  "room_start_dates": {"second": "2026-09-16"}}
        require_room_active(config, "first", self.now)
        with self.assertRaises(ValueError):
            require_room_active(config, "second", datetime(2026, 9, 15, 23, 59, tzinfo=KST))
        require_room_active(config, "second", datetime(2026, 9, 16, 7, 40, tzinfo=KST))
        for starts in ({"other": "2026-09-16"}, {"second": "tomorrow"}, []):
            with self.assertRaises(ValueError):
                require_room_active(config | {"room_start_dates": starts}, "second", self.now)


if __name__ == "__main__":
    unittest.main()
