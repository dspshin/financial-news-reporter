import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import morning_delivery_status as delivery
from kakao_morning_state import receipt_path


class DeliveryCompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / "2026-09-17-am"
        self.folder.mkdir()
        (self.folder / "briefing.png").write_bytes(b"reviewed image")
        self.image_hash = delivery.sha256(self.folder / "briefing.png")
        (self.root / ".kakao_morning.json").write_text(json.dumps({"room_names": ["first", "second"]}))
        self.now = datetime(2026, 9, 17, 8, 0, tzinfo=delivery.KST)
        self.put("telegram", "sent", message_id=307)

    def put(self, room, status, **extra):
        path = (self.root / ".morning_image_delivery" / f"{self.folder.name}-channel.json"
                if room == "telegram" else receipt_path(self.root / ".morning_kakao_delivery", self.folder, room))
        path.parent.mkdir(exist_ok=True)
        record = {"edition": self.folder.name, "image_sha256": self.image_hash,
                  "status": status, "evidence": "Observed outgoing image", "observed_at_kst": self.now.isoformat()}
        if room != "telegram":
            record["room"] = room
        path.write_text(json.dumps(record | extra))
        return path

    def sync(self):
        return delivery.reconcile(self.folder, self.root, self.now)

    def test_actual_incident_stale_summary_and_pending_file_selection_cannot_complete(self):
        (self.folder / "run-status.json").write_text('{"status":"completed"}')
        self.put("first", "pending", ui_phase="file_selected")
        state = self.sync()
        self.assertEqual(state["status"], "incomplete")
        self.assertEqual(state["next_target"], "first")
        self.assertEqual(state["next_action"], "inspect_ui_before_resume")
        self.assertEqual(state["deliveries"]["telegram"]["message_id"], 307)
        self.assertEqual(json.loads((self.folder / "run-status.json").read_text()), state)

    def test_first_sent_advances_to_second_without_reposting(self):
        self.put("first", "sent")
        state = self.sync()
        self.assertEqual((state["next_target"], state["next_action"]), ("second", "open_kakao_room"))

    def test_unknown_or_possibly_sent_attempt_requires_inspection(self):
        for phase in (None, "send_requested"):
            self.put("first", "pending", ui_phase=phase)
            self.assertEqual(self.sync()["next_action"], "inspect_ui_no_resend")

    def test_all_sent_or_allowed_skips_complete_only_with_valid_review(self):
        self.put("first", "sent")
        self.put("second", "skipped", reason="not_logged_in")
        with patch.object(delivery, "validate_bundle"):
            self.assertEqual(delivery.completion_check(self.folder, self.root, self.now)["status"], "completed")
        with patch.object(delivery, "validate_bundle", side_effect=ValueError("review mismatch")):
            self.assertEqual(delivery.completion_check(self.folder, self.root, self.now)["status"], "incomplete")

    def test_both_logged_out_skips_are_terminal(self):
        self.put("first", "skipped", reason="not_logged_in")
        self.assertEqual(self.sync()["next_action"], "verify_remaining_skip")
        self.put("second", "skipped", reason="not_logged_in")
        self.assertEqual(self.sync()["status"], "completed")

    def test_invalid_receipts_cannot_hide_behind_success(self):
        self.put("second", "sent")
        for change in ({"status": "uncertain"}, {"image_sha256": "wrong"},
                       {"edition": "2026-09-16-am"}, {"evidence": ""},
                       {"status": "skipped", "reason": "timeout"}):
            self.put("first", "sent", **{k: v for k, v in change.items() if k != "status"})
            if "status" in change:
                self.put("first", change["status"], **{k: v for k, v in change.items() if k != "status"})
            self.assertEqual(self.sync()["status"], "incomplete")
        path = self.put("first", "sent")
        path.write_text("{broken")
        self.assertEqual(self.sync()["deliveries"]["first"]["status"], "invalid")

    def test_sent_after_skipped_predecessor_is_invalid(self):
        self.put("first", "skipped", reason="os_locked")
        self.put("second", "sent")
        self.assertEqual(self.sync()["next_action"], "inspect_receipt_order")

    def test_telegram_uncertain_or_conflicting_receipt_blocks_completion(self):
        self.put("first", "sent")
        self.put("second", "sent")
        self.put("telegram", "uncertain")
        self.assertEqual(self.sync()["status"], "incomplete")
        path = self.put("telegram", "sent", message_id=307)
        path.with_name(f"{self.folder.name}-other.json").write_text(path.read_text())
        self.assertEqual(self.sync()["deliveries"]["telegram"]["status"], "invalid")

    def test_future_room_is_not_required_before_activation(self):
        (self.root / ".kakao_morning.json").write_text(json.dumps({
            "room_names": ["first", "second"], "room_start_dates": {"second": "2026-09-18"}}))
        self.put("first", "sent")
        self.assertEqual(self.sync()["status"], "completed")

    def test_completion_cli_returns_nonzero_for_partial_delivery(self):
        with patch("sys.argv", ["delivery", "check", "--bundle", str(self.folder)]), \
                patch.object(delivery, "completion_check", return_value={"status": "incomplete"}), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                delivery.main()
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
