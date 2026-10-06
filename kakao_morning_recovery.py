"""Persist bounded UI recovery and room deferrals; never operates UI or sends."""

import json

from publish_morning_image import save_receipt, sha256, validate_bundle


MAX_RETRIES = 3
OPERATIONS = {"state_read", "navigate", "file_select", "attachment", "optional_notice"}
DEFER_REASONS = {"retry_exhausted", "delivery_unconfirmed", "receipt_invalid",
                 "tool_unavailable", "room_unavailable"}
PHASES = {"not_started", "room_verified", "file_selected", "attachment_ready",
          "send_requested", "unknown"}


def recovery_path(state_root, folder):
    return state_root / f"{folder.name}-recovery.json"


def read_recovery(folder, state_root):
    path = recovery_path(state_root, folder)
    if not path.exists():
        return {"edition": folder.name, "image_sha256": sha256(folder / "briefing.png"),
                "rooms": {}}
    journal = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(journal, dict) or journal.get("edition") != folder.name
            or journal.get("image_sha256") != sha256(folder / "briefing.png")
            or not isinstance(journal.get("rooms"), dict)):
        raise ValueError("Invalid recovery journal; preserve it without retrying")
    for room, entry in journal["rooms"].items():
        if (not isinstance(room, str) or not room.strip() or not isinstance(entry, dict)
                or not isinstance(entry.get("retries", {}), dict)):
            raise ValueError("Invalid recovery room")
        for key, retry in entry.get("retries", {}).items():
            parts = key.split(":")
            if (len(parts) != 2 or parts[0] not in PHASES or parts[1] not in OPERATIONS
                    or not isinstance(retry, dict) or type(retry.get("count")) is not int
                    or not 1 <= retry["count"] <= MAX_RETRIES
                    or not retry.get("evidence") or not retry.get("observed_at_kst")):
                raise ValueError("Invalid retry count; do not reset it")
        deferred = entry.get("deferred")
        if deferred is not None and (not isinstance(deferred, dict)
                or deferred.get("reason") not in DEFER_REASONS
                or not deferred.get("evidence") or not deferred.get("observed_at_kst")
                or "receipt_sha256" not in deferred):
            raise ValueError("Invalid room deferral")
    return journal


def current_record(folder, state_root, room):
    from kakao_morning_state import receipt_path
    from morning_delivery_status import read_record
    path = receipt_path(state_root, folder, room)
    return read_record(path, folder, sha256(folder / "briefing.png"), room), path


def retry_key(record, operation):
    phase = record.get("ui_phase", "not_started" if record["status"] == "not_started" else "unknown")
    return f"{phase if phase in PHASES else 'unknown'}:{operation}"


def valid_deferral(journal, room, path):
    deferred = journal["rooms"].get(room, {}).get("deferred")
    if deferred is None:
        return False
    fingerprint = sha256(path) if path.exists() else None
    if deferred["receipt_sha256"] != fingerprint:
        raise ValueError("Deferred receipt changed; inspect without resending")
    return True


def prepare(folder, state_root, room, evidence, now):
    from kakao_morning_state import require_telegram_success
    if not evidence.strip():
        raise ValueError("Actual recovery evidence is required")
    validate_bundle(folder, now)
    require_telegram_success(folder)
    journal = read_recovery(folder, state_root)
    record, path = current_record(folder, state_root, room)
    if record["status"] in {"sent", "skipped"}:
        raise ValueError("Preserve the completed room; no retry or deferral")
    return journal, record, path


def record_retry(folder, state_root, room, operation, evidence, now):
    if operation not in OPERATIONS:
        raise ValueError("Only UI observation/navigation recovery is retryable; never retry send")
    journal, record, path = prepare(folder, state_root, room, evidence, now)
    if valid_deferral(journal, room, path):
        raise ValueError("This room was deferred; continue to the next room")
    if operation in {"file_select", "attachment"} and (
            record["status"] != "pending"
            or record.get("ui_phase") == "send_requested"
            or (record["status"] == "pending" and record.get("ui_phase") not in
                {"room_verified", "file_selected", "attachment_ready"})):
        raise ValueError("A matching presend pending attempt is required; do not attach again")
    entry = journal["rooms"].setdefault(room, {"retries": {}})
    key = retry_key(record, operation)
    count = entry["retries"].get(key, {}).get("count", 0)
    if count >= MAX_RETRIES:
        raise ValueError("Three retries exhausted; defer this room instead of restarting")
    retry = {"count": count + 1, "evidence": evidence,
             "observed_at_kst": now.isoformat()}
    entry["retries"][key] = retry
    state_root.mkdir(parents=True, exist_ok=True)
    save_receipt(recovery_path(state_root, folder), journal)
    return {"room": room, "operation": operation, "phase": key.split(":")[0],
            "retry": retry["count"], "max_retries": MAX_RETRIES}


def defer_room(folder, state_root, room, reason, evidence, now, operation=None):
    if reason not in DEFER_REASONS:
        raise ValueError("Use a room deferral reason; authentication skips are separate")
    journal, record, path = prepare(folder, state_root, room, evidence, now)
    entry = journal["rooms"].setdefault(room, {"retries": {}})
    if entry.get("deferred") is not None:
        raise ValueError("Preserve the existing deferral and continue to the next room")
    if reason == "retry_exhausted":
        if operation not in OPERATIONS or entry["retries"].get(
                retry_key(record, operation), {}).get("count", 0) != MAX_RETRIES:
            raise ValueError("Retry exhaustion requires three durable retries of this step")
    if reason == "delivery_unconfirmed" and not (
            record["status"] in {"uncertain", "rejected"}
            or record["status"] == "pending" and record.get("ui_phase") == "send_requested"):
        raise ValueError("No possibly sent attempt to defer")
    if reason == "receipt_invalid" and record["status"] != "invalid":
        raise ValueError("Only a damaged receipt uses receipt_invalid")
    entry["deferred"] = {"reason": reason, "evidence": evidence,
                         "observed_at_kst": now.isoformat(),
                         "receipt_sha256": sha256(path) if path.exists() else None,
                         "receipt_status": record["status"], "ui_phase": record.get("ui_phase")}
    state_root.mkdir(parents=True, exist_ok=True)
    save_receipt(recovery_path(state_root, folder), journal)
    return {"room": room, "status": "deferred", **entry["deferred"]}
