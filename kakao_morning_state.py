"""Track reviewed KakaoTalk UI deliveries; this script never controls UI or sends."""

import argparse
import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path

from publish_morning_image import KST, ROOT, save_receipt, sha256, validate_bundle


def receipt_path(state_root, folder, room):
    key = hashlib.sha256(room.encode()).hexdigest()[:16]
    return state_root / f"{folder.name}-{key}.json"


def configured_rooms(config):
    rooms = config.get("room_names")
    if rooms is None:
        rooms = [config.get("room_name")]
    if (not isinstance(rooms, list) or not rooms
            or any(not isinstance(room, str) or not room.strip() or room != room.strip()
                   for room in rooms)
            or len(set(rooms)) != len(rooms)):
        raise ValueError("Set unique exact room_names in delivery order in .kakao_morning.json")
    if "room_name" in config and config["room_name"] != rooms[0]:
        raise ValueError("Legacy room_name must match the first room_names entry")
    return rooms


def select_room(rooms, requested):
    if requested is None and len(rooms) == 1:
        return rooms[0]
    if requested not in rooms:
        raise ValueError("Specify --room with an exact configured destination")
    return requested


def require_room_active(config, room, now):
    starts = config.get("room_start_dates", {})
    if not isinstance(starts, dict):
        raise ValueError("room_start_dates must map room names to YYYY-MM-DD")
    for name, value in starts.items():
        if name not in configured_rooms(config) or not isinstance(value, str):
            raise ValueError("Invalid room_start_dates entry")
        try:
            start = date.fromisoformat(value)
        except ValueError:
            raise ValueError("Room start date must be YYYY-MM-DD") from None
        if start.isoformat() != value:
            raise ValueError("Room start date must be YYYY-MM-DD")
        if name == room and now.astimezone(KST).date() < start:
            raise ValueError(f"This room's delivery starts on {value} KST; do not send earlier")


def require_previous_rooms(folder, state_root, previous_rooms):
    image_hash = sha256(folder / "briefing.png")
    receipts = []
    for room in previous_rooms:
        path = receipt_path(state_root, folder, room)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("Previous KakaoTalk destination has no valid sent receipt") from None
        if (not isinstance(record, dict) or record.get("status") != "sent"
                or record.get("room") != room or record.get("edition") != folder.name
                or record.get("image_sha256") != image_hash):
            raise ValueError("Previous KakaoTalk destination must confirm the same image first")
        receipts.append(path.name)
    return receipts


def require_telegram_success(folder):
    image_hash = sha256(folder / "briefing.png")
    for path in (ROOT / ".morning_image_delivery").glob(f"{folder.name}-*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        if (record.get("status") == "sent"
                and record.get("edition") == folder.name
                and record.get("image_sha256") == image_hash
                and type(record.get("message_id")) is int
                and record["message_id"] > 0):
            return path.name
    raise ValueError("Send this reviewed image to Telegram successfully before KakaoTalk")


def begin(folder, room, observed_room, state_root, now, previous_rooms=()):
    if observed_room != room:
        raise ValueError("Observed room does not match configured destination")
    validate_bundle(folder, now)
    telegram_receipt = require_telegram_success(folder)
    previous_receipts = require_previous_rooms(folder, state_root, previous_rooms)
    path = receipt_path(state_root, folder, room)
    state_root.mkdir(parents=True, exist_ok=True)
    record = {
        "status": "pending", "room": room, "edition": folder.name,
        "ui_phase": "room_verified",
        "image_sha256": sha256(folder / "briefing.png"),
        "telegram_receipt": telegram_receipt,
        "previous_kakao_receipts": previous_receipts,
        "attempted_at_kst": now.isoformat(),
    }
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise ValueError("Delivery record already exists; do not send again") from None
    return record


def checkpoint(folder, room, observed_room, state_root, phase, evidence, now):
    from morning_delivery_status import UI_PHASES
    if observed_room != room or phase not in UI_PHASES or not evidence.strip():
        raise ValueError("Exact observed room, UI phase and evidence are required")
    path = receipt_path(state_root, folder, room)
    record = json.loads(path.read_text(encoding="utf-8"))
    if (record.get("status") != "pending" or record.get("room") != room
            or record.get("edition") != folder.name
            or record.get("image_sha256") != sha256(folder / "briefing.png")):
        raise ValueError("Only the same pending image/room may be checkpointed")
    prior = record.get("ui_phase")
    if prior not in UI_PHASES or prior == "send_requested":
        raise ValueError("Send may already have occurred; inspect UI without resending")
    if UI_PHASES.index(phase) != UI_PHASES.index(prior) + 1:
        raise ValueError("UI phases must advance one step without resetting an attempt")
    record.update(ui_phase=phase, ui_observed_at_kst=now.isoformat(), ui_evidence=evidence)
    save_receipt(path, record)
    return record


def skip(folder, room, state_root, reason, evidence, now):
    from morning_delivery_status import SKIP_REASONS, PRESEND_PHASES
    if reason not in SKIP_REASONS or not evidence.strip():
        raise ValueError("An allowed skip reason and actual observed evidence are required")
    validate_bundle(folder, now)
    telegram_receipt = require_telegram_success(folder)
    path = receipt_path(state_root, folder, room)
    image_hash = sha256(folder / "briefing.png")
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        if (record.get("status") != "pending" or record.get("room") != room
                or record.get("edition") != folder.name or record.get("image_sha256") != image_hash
                or record.get("ui_phase") not in PRESEND_PHASES):
            raise ValueError("Cannot skip a completed, damaged or possibly sent attempt")
        record.update(status="skipped", reason=reason, evidence=evidence, observed_at_kst=now.isoformat())
        save_receipt(path, record)
    else:
        state_root.mkdir(parents=True, exist_ok=True)
        record = {"status": "skipped", "room": room, "edition": folder.name,
                  "image_sha256": image_hash, "telegram_receipt": telegram_receipt,
                  "reason": reason, "evidence": evidence, "observed_at_kst": now.isoformat()}
        with path.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
    return record


def finish(folder, room, state_root, status, evidence, now):
    if status not in {"sent", "uncertain"} or not evidence.strip():
        raise ValueError("Outcome and observed UI evidence are required")
    path = receipt_path(state_root, folder, room)
    record = json.loads(path.read_text(encoding="utf-8"))
    if (record.get("status") != "pending" or record.get("room") != room
            or record.get("edition") != folder.name):
        raise ValueError("Only the matching pending attempt may be resolved")
    if record.get("image_sha256") != sha256(folder / "briefing.png"):
        raise ValueError("Image changed during delivery; inspect without resending")
    record.update(status=status, observed_at_kst=now.isoformat(), evidence=evidence)
    save_receipt(path, record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "begin", "checkpoint", "skip", "sent", "uncertain"))
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--room", help="Exact configured room; required for mutations with multiple rooms")
    parser.add_argument("--observed-room")
    parser.add_argument("--evidence", default="")
    parser.add_argument("--phase")
    parser.add_argument("--reason")
    args = parser.parse_args()
    now = datetime.now(KST)
    folder = (args.bundle or ROOT / "output" / f"{now.date().isoformat()}-am").resolve()
    config = json.loads((ROOT / ".kakao_morning.json").read_text(encoding="utf-8"))
    rooms = configured_rooms(config)
    state_root = ROOT / ".morning_kakao_delivery"
    if args.action == "status" and args.room is None and len(rooms) > 1:
        records = []
        for room in rooms:
            path = receipt_path(state_root, folder, room)
            records.append(json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
                "status": "not_started", "room": room, "edition": folder.name,
            })
        print(json.dumps({"edition": folder.name, "rooms": records}, ensure_ascii=False))
        return
    room = select_room(rooms, args.room)
    if args.action == "status":
        path = receipt_path(state_root, folder, room)
        result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "status": "not_started", "room": room, "edition": folder.name,
        }
    elif args.action == "begin":
        require_room_active(config, room, now)
        result = begin(folder, room, args.observed_room, state_root, now,
                       previous_rooms=rooms[:rooms.index(room)])
    elif args.action == "checkpoint":
        require_room_active(config, room, now)
        validate_bundle(folder, now)
        result = checkpoint(folder, room, args.observed_room, state_root,
                            args.phase, args.evidence, now)
    elif args.action == "skip":
        require_room_active(config, room, now)
        result = skip(folder, room, state_root, args.reason, args.evidence, now)
    else:
        result = finish(folder, room, state_root, args.action, args.evidence, now)
    if args.action != "status":
        from morning_delivery_status import reconcile
        reconcile(folder, ROOT)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
