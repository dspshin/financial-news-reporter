import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import image_delivery_edition as edition
import kakao_morning_recovery as recovery
import kakao_morning_state as kakao
import morning_delivery_status as delivery
import publish_closing_image as closing


class ClosingKakaoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root / "2026-10-13-pm"
        self.folder.mkdir()
        self.now = datetime(2026, 10, 13, 17, 0, tzinfo=closing.KST)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (1254).to_bytes(4, "big") * 2
        (self.folder / "briefing.png").write_bytes(png)
        for name in closing.ARTIFACTS:
            if name != "briefing.png":
                (self.folder / name).write_text("verified")
        (self.folder / "caption.txt").write_text("2026-10-13 마감 시황 · 16:20 KST")
        self.qa = {
            "status": "passed", "date": "2026-10-13",
            "cutoff": "2026-10-13T16:20:00+09:00",
            "regular_session_closed_at": "2026-10-13T15:30:00+09:00",
            "qa_at_kst": "2026-10-13T16:40:00+09:00",
            "market_day": {"date": "2026-10-13", "status": "open_and_regular_closed",
                           "checked_at": "2026-10-13T16:21:00+09:00",
                           "sources": ["https://data.krx.co.kr/"]},
            "checks": {"visual_match": True},
            "sha256": {n: closing.sha256(self.folder / n) for n in closing.ARTIFACTS},
        }
        self.save_qa()
        self.rooms = ["금복회 장자풍도 60대下", "x삼성 투자방", "송골매 허리", "사랑해요♡♥"]
        config = {"room_names": self.rooms,
                  "direct_delivery": {"effective_from": "2026-10-07", "room_names": self.rooms,
                                      "max_retries": 3, "continue_on_room_failure": True}}
        (self.root / ".kakao_morning.json").write_text(json.dumps(config))
        self.telegram = self.root / ".closing_image_delivery" / f"{self.folder.name}-channel.json"
        self.telegram.parent.mkdir()
        self.telegram_record = {"edition": self.folder.name, "status": "sent", "message_id": 340,
                                "image_sha256": closing.sha256(self.folder / "briefing.png")}
        self.telegram.write_text(json.dumps(self.telegram_record))
        self.state_root = self.root / ".closing_kakao_delivery"
        root_patch = patch.object(kakao, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def save_qa(self):
        (self.folder / "qa.json").write_text(json.dumps(self.qa))

    def invoke(self, action, room=None, *args):
        argv = ["kakao", action, "--bundle", str(self.folder), *args]
        if room:
            argv.extend(["--room", room])
        with patch("sys.argv", argv), patch.object(kakao, "datetime") as clock, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            clock.now.return_value = self.now
            kakao.main()
        return json.loads(output.getvalue())

    def start(self, room):
        return self.invoke("begin", room, "--observed-room", room)

    def advance(self, room):
        for phase in ("file_selected", "attachment_ready", "send_requested"):
            self.invoke("checkpoint", room, "--observed-room", room,
                        "--phase", phase, "--evidence", "Actual current UI verification")

    def check(self):
        return delivery.completion_check(self.folder, self.root, self.now)

    def test_pm_sequence_and_completion_do_not_read_or_modify_am_receipts(self):
        am = self.root / ".morning_kakao_delivery"
        am.mkdir()
        morning_record = am / "2026-10-13-am-existing.json"
        morning_record.write_text('{"status":"sent","edition":"2026-10-13-am"}')
        original = morning_record.read_bytes()
        self.assertEqual(self.check()["next_target"], self.rooms[0])
        self.assertEqual(self.check()["status"], "incomplete")
        for room in self.rooms:
            self.start(room)
            self.advance(room)
            self.invoke("sent", room, "--evidence", "Today matching outgoing image visible")
        state = self.check()
        self.assertEqual(state["status"], "completed")
        self.assertEqual(list(state["deliveries"]), ["telegram", *self.rooms])
        self.assertTrue(state["all_targets_handled"])
        self.assertTrue(state["order_valid"])
        self.assertEqual(morning_record.read_bytes(), original)
        self.assertEqual(list(am.iterdir()), [morning_record])
        self.assertEqual(len(list(self.state_root.glob("*.json"))), 4)

    def test_am_telegram_success_cannot_authorize_pm_kakao(self):
        self.telegram.unlink()
        am = self.root / ".morning_image_delivery"
        am.mkdir()
        # Even a misplaced matching PM receipt in the AM store is not accepted.
        (am / self.telegram.name).write_text(json.dumps(self.telegram_record))
        with self.assertRaisesRegex(ValueError, "closing image"):
            self.start(self.rooms[0])
        self.assertFalse(self.state_root.exists())
        self.assertEqual(self.check()["deliveries"]["telegram"]["status"], "not_started")

    def test_missing_or_conflicting_pm_telegram_receipts_block_begin(self):
        for record in (self.telegram_record | {"status": "pending"},
                       self.telegram_record | {"image_sha256": "wrong"},
                       self.telegram_record | {"message_id": None}):
            self.telegram.write_text(json.dumps(record))
            with self.assertRaises((ValueError, RuntimeError)):
                self.start(self.rooms[0])
            self.assertFalse(self.state_root.exists())
        self.telegram.write_text(json.dumps(self.telegram_record))
        conflict = self.telegram.with_name(f"{self.folder.name}-other.json")
        conflict.write_text("{broken")
        with self.assertRaisesRegex(RuntimeError, "Multiple"):
            self.start(self.rooms[0])
        self.assertEqual(self.check()["deliveries"]["telegram"]["status"], "invalid")

    def test_closing_time_and_qa_are_used_instead_of_am_validation(self):
        with self.assertRaisesRegex(ValueError, "after 16:20"):
            kakao.begin(self.folder, self.rooms[0], self.rooms[0], self.state_root,
                        self.now.replace(hour=16, minute=19))
        self.qa["cutoff"] = "2026-10-13T07:35:00+09:00"
        self.save_qa()
        with self.assertRaisesRegex(ValueError, "Closing review cutoff"):
            self.start(self.rooms[0])
        self.assertEqual(self.check()["stage"], "review_invalid")
        self.assertFalse(self.state_root.exists())

    def test_three_retries_defer_pm_room_and_allow_next_without_marking_complete(self):
        first = self.rooms[0]
        self.start(first)
        receipt = kakao.receipt_path(self.state_root, self.folder, first)
        original = receipt.read_bytes()
        for count in range(1, 4):
            result = self.invoke("retry", first, "--operation", "navigate", "--evidence", "UI error")
            self.assertEqual(result["retry"], count)
        journal = recovery.recovery_path(self.state_root, self.folder)
        before = journal.read_bytes()
        with self.assertRaisesRegex(ValueError, "Three retries exhausted"):
            self.invoke("retry", first, "--operation", "navigate", "--evidence", "UI error")
        self.assertEqual(journal.read_bytes(), before)
        self.invoke("defer", first, "--reason", "retry_exhausted", "--operation", "navigate",
                    "--evidence", "Three attempts failed")
        self.assertEqual(receipt.read_bytes(), original)
        self.assertEqual(self.check()["next_target"], self.rooms[1])
        for room in self.rooms[1:]:
            self.start(room)
            self.advance(room)
            self.invoke("sent", room, "--evidence", "Actual matching new outgoing image")
        self.assertEqual(self.check()["status"], "incomplete")
        self.assertEqual(self.check()["next_action"], "report_deferred_rooms")
        self.assertTrue(self.check()["all_targets_handled"])
        self.assertFalse((self.root / ".morning_kakao_delivery").exists())

    def test_possibly_sent_pm_image_cannot_be_reattached_or_skipped(self):
        first = self.rooms[0]
        self.start(first)
        self.advance(first)
        for action, args in (("begin", ["--observed-room", first]),
                             ("retry", ["--operation", "attachment", "--evidence", "Unknown send"]),
                             ("skip", ["--reason", "os_locked", "--evidence", "Lock screen"])):
            with self.assertRaises(ValueError):
                self.invoke(action, first, *args)
        self.invoke("uncertain", first, "--evidence", "Could not verify new outgoing image")
        self.invoke("defer", first, "--reason", "delivery_unconfirmed", "--evidence", "Send unclear")
        self.assertEqual(self.check()["next_target"], self.rooms[1])

    def test_allowed_login_skips_are_separate_from_all_sent(self):
        for room in self.rooms:
            self.invoke("skip", room, "--reason", "not_logged_in", "--evidence", "Login screen")
        state = self.check()
        self.assertEqual(state["status"], "completed")
        self.assertTrue(all(state["deliveries"][r]["status"] == "skipped" for r in self.rooms))

    def test_edition_names_and_receipt_stores_are_explicit(self):
        self.assertEqual(edition.kakao_state_name(self.folder), ".closing_kakao_delivery")
        self.assertEqual(edition.telegram_state_name(self.folder), ".closing_image_delivery")
        for name in ("2026-10-13", "20261013-pm", "2026-10-13-other"):
            with self.assertRaises(ValueError):
                edition.bundle_edition(Path(name))


if __name__ == "__main__":
    unittest.main()
