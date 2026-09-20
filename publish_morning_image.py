"""Publish a reviewed morning PNG to the repository's general Telegram channel."""

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import dotenv_values
from morning_market_day import validate_market_day

ROOT = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def credentials():
    config = dotenv_values(ROOT / ".env")
    token = config.get("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    channel = config.get("TELEGRAM_CHANNEL_ID") or os.getenv("TELEGRAM_CHANNEL_ID")
    if not token or not channel:
        raise ValueError("General Telegram credentials are missing")
    return token, channel


class Rejected(RuntimeError):
    """Telegram explicitly rejected the request (no message created)."""


def api(token, method, data=None, files=None):
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/{method}",
            data=data, files=files, timeout=(15, 60),
        )
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        # Request exception messages may contain the credential-bearing URL.
        raise RuntimeError(f"Telegram response uncertain ({type(error).__name__})") from None
    if payload.get("ok") is False:
        raise Rejected(f"Telegram rejected request (code {payload.get('error_code', response.status_code)})")
    if not response.ok or payload.get("ok") is not True or "result" not in payload:
        raise RuntimeError("Telegram response uncertain; inspect channel before retrying")
    return payload["result"]


def check_channel(token, channel):
    bot = api(token, "getMe")
    chat = api(token, "getChat", {"chat_id": channel})
    member = api(token, "getChatMember", {"chat_id": chat["id"], "user_id": bot["id"]})
    if chat.get("type") != "channel":
        raise ValueError("Configured general destination is not a Telegram channel")
    allowed = member.get("status") == "creator" or (
        member.get("status") == "administrator" and member.get("can_post_messages")
    )
    if not allowed:
        raise ValueError("Bot cannot post to the configured general channel")
    return chat


def validate_bundle(folder, now):
    day = now.date().isoformat()
    if now.weekday() >= 5 or (now.hour, now.minute) < (7, 40):
        raise ValueError("Publishing is restricted to weekdays at or after 07:40 KST")
    if folder.name != f"{day}-am":
        raise ValueError("Bundle must belong to today's KST morning edition")
    qa = json.loads((folder / "qa.json").read_text(encoding="utf-8"))
    if qa.get("status") != "passed" or qa.get("date") != day:
        raise ValueError("Today's visual/data review must be marked passed")
    if qa.get("cutoff") != f"{day}T07:40:00+09:00":
        raise ValueError("Review cutoff must be today's 07:40 Asia/Seoul")
    validate_market_day(qa.get("market_day"), now)
    for filename in ("briefing.png", "manuscript.txt", "sources.md", "caption.txt"):
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
    caption = (folder / "caption.txt").read_text(encoding="utf-8").strip()
    if len(caption.encode("utf-16-le")) // 2 > 1024 or day not in caption or "07:40" not in caption:
        raise ValueError("Caption needs edition date and 07:40 cutoff, within 1024 characters")
    return caption


def save_receipt(path, receipt):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(receipt, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def publish(folder, token, channel, caption, state_root, on_pending=None):
    state_root.mkdir(parents=True, exist_ok=True)
    destination_key = hashlib.sha256(str(channel).encode()).hexdigest()[:16]
    receipt_path = state_root / f"{folder.name}-{destination_key}.json"
    receipt = {"status": "pending", "edition": folder.name,
               "image_sha256": sha256(folder / "briefing.png"),
               "attempted_at": datetime.now(KST).isoformat()}
    try:
        with receipt_path.open("x", encoding="utf-8") as handle:
            json.dump(receipt, handle)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        previous = json.loads(receipt_path.read_text(encoding="utf-8"))
        if previous.get("status") == "sent":
            return {"status": "already_sent", "message_id": previous["message_id"]}
        raise RuntimeError("Existing unresolved/rejected delivery: inspect receipt and channel; do not resend automatically")
    if on_pending is not None:
        # Persist the outstanding action before the network call. A failure here
        # leaves a pending receipt and must not result in an automatic resend.
        on_pending()
    try:
        with (folder / "briefing.png").open("rb") as photo:
            result = api(token, "sendPhoto", {"chat_id": channel, "caption": caption},
                         {"photo": ("briefing.png", photo, "image/png")})
        if not isinstance(result.get("message_id"), int) or not result.get("photo"):
            raise RuntimeError("Telegram response lacks photo receipt")
        receipt.update(status="sent", message_id=result["message_id"],
                       sent_at=datetime.now(KST).isoformat())
        save_receipt(receipt_path, receipt)
        return {"status": "sent", "message_id": result["message_id"]}
    except Exception as error:
        receipt["status"] = "rejected" if isinstance(error, Rejected) else "uncertain"
        save_receipt(receipt_path, receipt)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-config", action="store_true", help="Read-only Telegram channel/permission check")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Validate files without a network request or sending")
    args = parser.parse_args()
    if args.check_config:
        token, channel = credentials()
        chat = check_channel(token, channel)
        print(json.dumps({"status": "ready", "channel_title": chat.get("title"),
                          "channel_username": chat.get("username"), "can_post": True}, ensure_ascii=False))
        return
    folder = args.bundle or ROOT / "output" / f"{datetime.now(KST).date().isoformat()}-am"
    caption = validate_bundle(folder.resolve(), datetime.now(KST))
    if args.dry_run:
        print(json.dumps({"status": "validated", "bundle": str(folder), "sent": False}))
        return
    token, channel = credentials()
    chat = check_channel(token, channel)
    from morning_delivery_status import reconcile
    try:
        result = publish(folder, token, chat["id"], caption, ROOT / ".morning_image_delivery",
                         on_pending=lambda: reconcile(folder, ROOT))
    finally:
        # Outside publish's exception handler: a summary write failure must
        # never turn an acknowledged Telegram success into an uncertain send.
        reconcile(folder, ROOT)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Avoid printing unexpected exception details containing credentials.
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        print(json.dumps({"status": "error", "message": message}, ensure_ascii=False))
        raise SystemExit(1)
