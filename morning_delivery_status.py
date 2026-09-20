"""Reconcile durable delivery receipts and gate completion. Never sends messages."""

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from publish_morning_image import ROOT, KST, save_receipt, sha256, validate_bundle


SKIP_REASONS = {"not_logged_in", "reauthentication", "login_unknown",
                "permission_unavailable", "os_locked"}
PRESEND_PHASES = ("room_verified", "file_selected", "attachment_ready")
UI_PHASES = PRESEND_PHASES + ("send_requested",)


def read_record(path, folder, image_hash, room=None):
    if not path.exists():
        return {"status": "not_started"}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(record, dict) or record.get("edition") != folder.name
                or record.get("image_sha256") != image_hash
                or (room is not None and record.get("room") != room)):
            raise ValueError("Receipt identity or image mismatch")
        status = record.get("status")
        if status not in {"sent", "pending", "uncertain", "rejected", "skipped"}:
            raise ValueError("Invalid receipt status")
        if status == "sent":
            if room is None:
                if type(record.get("message_id")) is not int or record["message_id"] <= 0:
                    raise ValueError("Missing Telegram message ID")
            elif not record.get("evidence") or not record.get("observed_at_kst"):
                raise ValueError("Missing observed KakaoTalk delivery evidence")
        if status == "skipped" and (room is None or record.get("reason") not in SKIP_REASONS
                or not record.get("evidence") or not record.get("observed_at_kst")
                or record.get("ui_phase") == "send_requested"):
            raise ValueError("Invalid skip evidence")
        return record
    except (OSError, ValueError, TypeError):
        return {"status": "invalid", "receipt": path.name}


def reconcile(folder, root=ROOT, now=None):
    # Import lazily: the publisher and Kakao CLI also call this module.
    from kakao_morning_state import configured_rooms, receipt_path
    now = now or datetime.now(KST)
    day = date.fromisoformat(folder.name.removesuffix("-am"))
    if folder.name != f"{day.isoformat()}-am":
        raise ValueError("Invalid morning bundle name")
    config = json.loads((root / ".kakao_morning.json").read_text(encoding="utf-8"))
    rooms = configured_rooms(config)
    starts = config.get("room_start_dates", {})
    if not isinstance(starts, dict) or any(room not in rooms for room in starts):
        raise ValueError("Invalid room start dates")
    active = [room for room in rooms if day >= date.fromisoformat(starts.get(room, "1970-01-01"))]
    image_hash = sha256(folder / "briefing.png")
    paths = list((root / ".morning_image_delivery").glob(f"{folder.name}-*.json"))
    # Multiple destinations/conflicting records require inspection; do not pick a success.
    telegram = (read_record(paths[0], folder, image_hash) if len(paths) == 1 else
                {"status": "invalid" if paths else "not_started"})
    deliveries = {"telegram": telegram}
    for room in active:
        deliveries[room] = read_record(
            receipt_path(root / ".morning_kakao_delivery", folder, room), folder, image_hash, room)
    complete = telegram["status"] == "sent" and all(
        deliveries[room]["status"] in {"sent", "skipped"} for room in active)
    # A later room may not have been sent after an unresolved or skipped predecessor.
    previous_sent = telegram["status"] == "sent"
    order_valid = True
    for room in active:
        if deliveries[room]["status"] == "sent" and not previous_sent:
            order_valid = False
        previous_sent = previous_sent and deliveries[room]["status"] == "sent"
    complete = complete and order_valid
    next_action = "complete"
    next_target = None
    if not order_valid:
        next_action = "inspect_receipt_order"
    elif not complete:
        for target, record in deliveries.items():
            status = record["status"]
            if status in {"sent", "skipped"}:
                continue
            next_target = target
            if target == "telegram":
                next_action = "publish_telegram" if status == "not_started" else "inspect_telegram_no_resend"
            elif status == "pending":
                next_action = ("inspect_ui_before_resume" if record.get("ui_phase") in PRESEND_PHASES
                               else "inspect_ui_no_resend")
            elif status == "not_started":
                # If a preceding room was skipped, only an observed skip remains possible.
                before = active[:active.index(target)]
                next_action = ("verify_remaining_skip" if any(deliveries[r]["status"] == "skipped" for r in before)
                               else "open_kakao_room")
            else:
                next_action = "inspect_ui_no_resend"
            break
    result = {"date": day.isoformat(), "edition": folder.name,
              "objective": "당일 이미지 검수와 Telegram·지정 카카오톡 대상 전송 완료",
              "stage": "delivery_completed" if complete else "delivery_incomplete",
              "status": "completed" if complete else "incomplete",
              "updated_at": now.isoformat(), "image_sha256": image_hash,
              "deliveries": deliveries, "next_target": next_target,
              "next_action": next_action, "order_valid": order_valid}
    save_receipt(folder / "run-status.json", result)
    return result


def completion_check(folder, root=ROOT, now=None):
    now = now or datetime.now(KST)
    result = reconcile(folder, root, now)
    try:
        validate_bundle(folder, now)
    except (OSError, ValueError, TypeError) as error:
        result.update(status="incomplete", stage="review_invalid",
                      next_action="inspect_review", review_error=str(error))
        save_receipt(folder / "run-status.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("sync", "check"))
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    folder = args.bundle.resolve()
    result = (completion_check if args.action == "check" else reconcile)(folder)
    print(json.dumps(result, ensure_ascii=False))
    if args.action == "check" and result["status"] != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False))
        raise SystemExit(2)
