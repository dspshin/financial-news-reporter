"""Track reviewed KakaoTalk UI deliveries; this script never controls UI or sends."""

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from publish_morning_image import KST, ROOT, save_receipt, sha256, validate_bundle


def receipt_path(state_root, folder, room):
    key = hashlib.sha256(room.encode()).hexdigest()[:16]
    return state_root / f"{folder.name}-{key}.json"


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


def begin(folder, room, observed_room, state_root, now):
    if observed_room != room:
        raise ValueError("Observed room does not match configured destination")
    validate_bundle(folder, now)
    telegram_receipt = require_telegram_success(folder)
    path = receipt_path(state_root, folder, room)
    state_root.mkdir(parents=True, exist_ok=True)
    record = {
        "status": "pending", "room": room, "edition": folder.name,
        "image_sha256": sha256(folder / "briefing.png"),
        "telegram_receipt": telegram_receipt,
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


def finish(folder, room, state_root, status, evidence, now):
    if status not in {"sent", "uncertain"} or not evidence.strip():
        raise ValueError("Outcome and observed UI evidence are required")
    path = receipt_path(state_root, folder, room)
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("status") != "pending" or record.get("room") != room:
        raise ValueError("Only the matching pending attempt may be resolved")
    if record.get("image_sha256") != sha256(folder / "briefing.png"):
        raise ValueError("Image changed during delivery; inspect without resending")
    record.update(status=status, observed_at_kst=now.isoformat(), evidence=evidence)
    save_receipt(path, record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "begin", "sent", "uncertain"))
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--observed-room")
    parser.add_argument("--evidence", default="")
    args = parser.parse_args()
    now = datetime.now(KST)
    folder = (args.bundle or ROOT / "output" / f"{now.date().isoformat()}-am").resolve()
    config = json.loads((ROOT / ".kakao_morning.json").read_text(encoding="utf-8"))
    room = config.get("room_name")
    if not isinstance(room, str) or not room.strip():
        raise ValueError("Set the exact room_name in .kakao_morning.json")
    state_root = ROOT / ".morning_kakao_delivery"
    if args.action == "status":
        path = receipt_path(state_root, folder, room)
        result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "status": "not_started", "room": room, "edition": folder.name,
        }
    elif args.action == "begin":
        result = begin(folder, room, args.observed_room, state_root, now)
    else:
        result = finish(folder, room, state_root, args.action, args.evidence, now)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
