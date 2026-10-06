import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import kakao_morning_recovery as recovery
import kakao_morning_state as kakao
import morning_delivery_status as delivery


class DirectRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root / "2026-10-07-am"
        self.folder.mkdir()
        (self.folder / "briefing.png").write_bytes(b"reviewed image")
        self.state_root = self.root / ".morning_kakao_delivery"
        self.now = datetime(2026, 10, 7, 8, 30, tzinfo=delivery.KST)
        self.config = {
            "room_names": ["first", "source", "third", "fourth"],
            "share_delivery": {"effective_from": "2026-10-03", "source_room": "source",
                               "target_rooms": ["first", "third", "fourth"]},
            "direct_delivery": {"effective_from": "2026-10-07",
                                "room_names": ["source", "first", "third", "fourth"],
                                "max_retries": 3, "continue_on_room_failure": True},
        }
        (self.root / ".kakao_morning.json").write_text(json.dumps(self.config))
        telegram = self.root / ".morning_image_delivery" / f"{self.folder.name}-channel.json"
        telegram.parent.mkdir()
        telegram.write_text(json.dumps({"edition": self.folder.name, "status": "sent",
                                        "image_sha256": delivery.sha256(self.folder / "briefing.png"),
                                        "message_id": 307}))
        for target in (patch.object(kakao, "ROOT", self.root),
                       patch.object(kakao, "validate_bundle"),
                       patch.object(recovery, "validate_bundle"),
                       patch.object(delivery, "validate_bundle")):
            target.start()
            self.addCleanup(target.stop)

    def put(self, room, status="pending", phase="room_verified"):
        path = kakao.receipt_path(self.state_root, self.folder, room)
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"edition": self.folder.name, "room": room,
                                   "image_sha256": delivery.sha256(self.folder / "briefing.png"),
                                   "status": status, "ui_phase": phase,
                                   "evidence": "Actual observation", "observed_at_kst": self.now.isoformat()}))
        return path

    def retry(self, operation="navigate", room="source"):
        return recovery.record_retry(self.folder, self.state_root, room, operation,
                                     "Observed transient UI error", self.now)

    def defer(self, reason, room="source", operation=None):
        return recovery.defer_room(self.folder, self.state_root, room, reason,
                                   "Observed unresolved room", self.now, operation)

    def sync(self):
        return delivery.reconcile(self.folder, self.root, self.now)

    def test_three_retries_persist_and_fourth_cannot_change_journal(self):
        self.put("source")
        for count in range(1, 4):
            self.assertEqual(self.retry()["retry"], count)
            journal = recovery.read_recovery(self.folder, self.state_root)
            self.assertEqual(journal["rooms"]["source"]["retries"]["room_verified:navigate"]["count"], count)
        path = recovery.recovery_path(self.state_root, self.folder)
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Three retries exhausted"):
            self.retry()
        self.assertEqual(path.read_bytes(), before)

    def test_exhausted_room_keeps_receipt_and_next_room_can_begin(self):
        path = self.put("source")
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "three durable retries"):
            self.defer("retry_exhausted", operation="navigate")
        for _ in range(3):
            self.retry()
        self.defer("retry_exhausted", operation="navigate")
        self.assertEqual(path.read_bytes(), before)
        state = self.sync()
        self.assertEqual((state["next_target"], state["next_action"]), ("first", "open_kakao_room"))
        record = kakao.begin(self.folder, "first", "first", self.state_root, self.now,
                             previous_rooms=["source"], allow_deferred=True)
        self.assertEqual(record["status"], "pending")
        with self.assertRaisesRegex(ValueError, "deferred"):
            kakao.checkpoint(self.folder, "source", "source", self.state_root,
                             "file_selected", "Current selection", self.now)

    def test_uncertain_send_allows_observation_but_never_send_or_new_attachment(self):
        path = self.put("source", phase="send_requested")
        before = path.read_bytes()
        for operation in ("send", "file_select", "attachment"):
            with self.assertRaises(ValueError):
                self.retry(operation)
        for _ in range(3):
            self.retry("state_read")
        self.defer("delivery_unconfirmed")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.sync()["next_target"], "first")
        with self.assertRaises(ValueError):
            kakao.skip(self.folder, "source", self.state_root, "os_locked", "Actual screen", self.now)

    def test_corrupt_receipt_can_be_isolated_without_erasing_it(self):
        path = self.put("source")
        path.write_text("{broken")
        self.defer("receipt_invalid")
        state = self.sync()
        self.assertEqual(state["deliveries"]["source"]["status"], "invalid")
        self.assertEqual(state["next_target"], "first")
        self.assertEqual(path.read_text(), "{broken")

    def test_deferrals_are_not_success_and_completion_cli_stays_nonzero(self):
        self.put("source", phase="send_requested")
        self.defer("delivery_unconfirmed")
        for room in ("first", "third", "fourth"):
            self.put(room, "sent", "send_requested")
        result = delivery.completion_check(self.folder, self.root, self.now)
        self.assertTrue(result["all_targets_handled"])
        self.assertTrue(result["order_valid"])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["next_action"], "report_deferred_rooms")
        self.assertEqual(result["deferred_rooms"], ["source"])
        with patch("sys.argv", ["delivery", "check", "--bundle", str(self.folder)]), \
                patch.object(delivery, "ROOT", self.root), \
                patch.object(delivery, "completion_check", return_value=result), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                delivery.main()
        self.assertEqual(error.exception.code, 2)

    def test_deferral_is_not_restarted_after_resume(self):
        self.defer("tool_unavailable")
        with self.assertRaisesRegex(ValueError, "deferred"):
            self.retry()
        with self.assertRaisesRegex(ValueError, "deferred"):
            kakao.begin(self.folder, "source", "source", self.state_root, self.now, allow_deferred=True)
        self.assertEqual(self.sync()["next_target"], "first")

    def test_changed_deferred_receipt_and_corrupt_journal_block_unsafe_recovery(self):
        path = self.put("source")
        self.defer("tool_unavailable")
        path.write_text(path.read_text() + " ")
        self.assertEqual(self.sync()["next_action"], "inspect_recovery_journal_no_retry")
        journal_path = recovery.recovery_path(self.state_root, self.folder)
        journal_path.write_text("{broken")
        with self.assertRaises(ValueError):
            self.retry()
        self.assertEqual(self.sync()["status"], "incomplete")
        self.assertEqual(journal_path.read_text(), "{broken")

    def test_completed_room_is_never_retried_or_deferred(self):
        path = self.put("source", "sent", "send_requested")
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.retry("state_read")
        with self.assertRaises(ValueError):
            self.defer("tool_unavailable")
        self.assertEqual(path.read_bytes(), before)

    def test_previous_room_requires_completion_or_durable_deferral(self):
        self.put("source")
        with self.assertRaisesRegex(ValueError, "previous room"):
            kakao.begin(self.folder, "first", "first", self.state_root, self.now,
                         previous_rooms=["source"], allow_deferred=True)
        self.assertFalse(kakao.receipt_path(self.state_root, self.folder, "first").exists())

    def test_global_tool_failure_handles_remaining_rooms_without_claiming_lock_or_success(self):
        self.put("source", "sent", "send_requested")
        for room in ("first", "third", "fourth"):
            self.defer("tool_unavailable", room)
        state = self.sync()
        self.assertTrue(state["all_targets_handled"])
        self.assertEqual(state["status"], "incomplete")
        self.assertEqual(state["deliveries"]["source"]["status"], "sent")
        self.assertEqual(state["deferred_rooms"], ["first", "third", "fourth"])
        self.assertEqual(state["next_action"], "report_deferred_rooms")
        for room in state["deferred_rooms"]:
            self.assertEqual(state["deliveries"][room]["status"], "not_started")
            self.assertEqual(state["deliveries"][room]["deferred"]["reason"], "tool_unavailable")

    def test_attachment_retry_requires_an_existing_presend_attempt(self):
        with self.assertRaises(ValueError):
            self.retry("attachment")
        self.put("source", phase="attachment_ready")
        self.assertEqual(self.retry("attachment")["retry"], 1)

    def test_historical_share_and_original_order_survive_future_direct_plan(self):
        self.assertEqual(kakao.delivery_rooms(self.config, date(2026, 10, 2)),
                         ["first", "source", "third", "fourth"])
        self.assertIsNotNone(kakao.share_plan(self.config, date(2026, 10, 6)))
        self.assertEqual(kakao.delivery_rooms(self.config, date(2026, 10, 7)),
                         ["source", "first", "third", "fourth"])
        self.assertIsNone(kakao.share_plan(self.config, date(2026, 10, 7)))
        for invalid in (3.0, True, 4):
            self.config["direct_delivery"]["max_retries"] = invalid
            with self.assertRaises(ValueError):
                kakao.direct_plan(self.config, date(2026, 10, 7))

    def test_cli_retry_and_defer_update_planner_without_operating_ui(self):
        for _ in range(3):
            with patch("sys.argv", ["kakao", "retry", "--bundle", str(self.folder),
                                    "--room", "source", "--operation", "navigate",
                                    "--evidence", "Actual transient navigation error"]), \
                    contextlib.redirect_stdout(io.StringIO()):
                kakao.main()
        with patch("sys.argv", ["kakao", "defer", "--bundle", str(self.folder),
                                "--room", "source", "--reason", "retry_exhausted",
                                "--operation", "navigate", "--evidence", "Three retries failed"]), \
                contextlib.redirect_stdout(io.StringIO()):
            kakao.main()
        state = json.loads((self.folder / "run-status.json").read_text())
        self.assertEqual(state["next_target"], "first")
        self.assertEqual(state["deferred_rooms"], ["source"])


if __name__ == "__main__":
    unittest.main()
