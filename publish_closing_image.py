"""Publish a reviewed closing PNG to the same general Telegram channel as AM."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from morning_market_day import validate_market_day
from publish_morning_image import (
    KST, ROOT, check_channel, credentials, publish, sha256,
)

ARTIFACTS = (
    "briefing.png", "manuscript.txt", "sources.md", "source-checks.json",
    "image-prompt.txt", "caption.txt",
)
STATE_NAME = ".closing_image_delivery"


def reviewed_time(value, label, day, now):
    try:
        observed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} needs an observation timestamp") from None
    if observed.tzinfo is None or observed.astimezone(KST).date() != day or observed > now:
        raise ValueError(f"{label} must be from today, not the future")
    return observed


def validate_bundle(folder, now):
    now = now.astimezone(KST)
    day = now.date()
    if now.weekday() >= 5 or (now.hour, now.minute) < (16, 20):
        raise ValueError("Closing publishing is restricted to weekdays after 16:20 KST")
    if folder.name != f"{day.isoformat()}-pm":
        raise ValueError("Bundle must belong to today's KST closing edition")
    qa = json.loads((folder / "qa.json").read_text(encoding="utf-8"))
    if qa.get("status") != "passed" or qa.get("date") != day.isoformat():
        raise ValueError("Today's closing visual/data review must be marked passed")
    cutoff = f"{day.isoformat()}T16:20:00+09:00"
    if qa.get("cutoff") != cutoff:
        raise ValueError("Closing review cutoff must be today's 16:20 Asia/Seoul")
    market = qa.get("market_day")
    if not isinstance(market, dict) or market.get("status") != "open_and_regular_closed":
        raise ValueError("Today's market opening and regular close must be verified")
    # Reuse the AM holiday, date, timestamp and official-source validation.
    validate_market_day({**market, "status": "open"}, now)
    closed = reviewed_time(qa.get("regular_session_closed_at"), "Regular close", day, now)
    checked = reviewed_time(market.get("checked_at"), "Market verification", day, now)
    reviewed = reviewed_time(qa.get("qa_at_kst"), "Visual review", day, now)
    if closed > datetime.fromisoformat(cutoff) or checked < closed or reviewed < checked:
        raise ValueError("Regular close and verification must precede the closing review")
    if reviewed < datetime.fromisoformat(cutoff):
        raise ValueError("Closing review must follow the 16:20 information cutoff")
    checks = qa.get("checks")
    if not isinstance(checks, dict) or not checks or any(v is not True for v in checks.values()):
        raise ValueError("Every required closing review check must pass")
    for filename in ARTIFACTS:
        path = folder / filename
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"Missing or empty artifact: {filename}")
        if qa.get("sha256", {}).get(filename) != sha256(path):
            raise ValueError(f"Artifact changed after review: {filename}")
    raw = (folder / "briefing.png").read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n" or len(raw) < 24 or len(raw) > 10_000_000:
        raise ValueError("A PNG under 10 MB is required")
    width, height = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
    if width != height or width < 1000 or width + height > 10000:
        raise ValueError("Image must be square, at least 1000 px and fit Telegram photo limits")
    manuscript = (folder / "manuscript.txt").read_text(encoding="utf-8")
    if any(word in manuscript for word in ("미확인", "검증 불가", "자료 없음")):
        raise ValueError("Unverified items must be omitted from the closing manuscript")
    caption = (folder / "caption.txt").read_text(encoding="utf-8").strip()
    if (len(caption.encode("utf-16-le")) // 2 > 1024 or day.isoformat() not in caption
            or "16:20" not in caption or "마감" not in caption):
        raise ValueError("Caption needs closing edition date and 16:20 cutoff, within 1024 characters")
    return caption


def delivery_status(folder, state_root):
    """Read receipts only; never delete a record or infer a successful send."""
    paths = list(state_root.glob(f"{folder.name}-*.json"))
    if not paths:
        return {"status": "not_started", "edition": folder.name}
    if len(paths) != 1:
        raise RuntimeError("Multiple closing delivery records: inspect before publishing")
    try:
        record = json.loads(paths[0].read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise RuntimeError("Damaged closing delivery record: do not resend automatically") from None
    if (not isinstance(record, dict) or record.get("edition") != folder.name
            or record.get("status") not in ("sent", "pending", "uncertain", "rejected")):
        raise RuntimeError("Invalid closing delivery record: do not resend automatically")
    if record.get("image_sha256") != sha256(folder / "briefing.png"):
        raise RuntimeError("Closing delivery belongs to a different image: inspect before publishing")
    result = {"status": record["status"], "edition": folder.name}
    if record["status"] == "sent":
        if type(record.get("message_id")) is not int or record["message_id"] <= 0:
            raise RuntimeError("Closing sent record lacks a valid message_id")
        result["message_id"] = record["message_id"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Validate without network requests or sending")
    parser.add_argument("--status", action="store_true", help="Read-only QA/hash and Telegram receipt check")
    parser.add_argument("--check-config", action="store_true", help="Read-only channel/permission check")
    args = parser.parse_args()
    if args.check_config:
        token, channel = credentials()
        chat = check_channel(token, channel)
        print(json.dumps({"status": "ready", "channel_title": chat.get("title"),
                          "channel_username": chat.get("username"), "can_post": True}, ensure_ascii=False))
        return
    folder = (args.bundle or ROOT / "output" / f"{datetime.now(KST).date().isoformat()}-pm").resolve()
    caption = validate_bundle(folder, datetime.now(KST))
    state_root = ROOT / STATE_NAME
    previous = delivery_status(folder, state_root)
    if args.status:
        print(json.dumps(previous))
        if previous["status"] != "sent":
            raise SystemExit(2)
        return
    if previous["status"] not in ("not_started", "sent"):
        raise RuntimeError("Existing unresolved/rejected closing delivery: inspect channel; do not resend automatically")
    if args.dry_run:
        print(json.dumps({"status": "validated", "bundle": str(folder), "sent": False,
                          "delivery_status": previous["status"]}))
        return
    if previous["status"] == "sent":
        print(json.dumps({"status": "already_sent", "message_id": previous["message_id"]}))
        return
    token, channel = credentials()
    chat = check_channel(token, channel)
    # Shared AM transport persists pending before sendPhoto and sent/message_id
    # afterwards. Its exclusive receipt creation prevents concurrent duplicates.
    result = publish(folder, token, chat["id"], caption, state_root)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        print(json.dumps({"status": "error", "message": message}, ensure_ascii=False))
        raise SystemExit(1)
