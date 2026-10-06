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


def direct_plan(config, day):
    plan = config.get("direct_delivery")
    if plan is None:
        return None
    rooms = configured_rooms(config)
    if (not isinstance(plan, dict) or not isinstance(plan.get("room_names"), list)
            or any(not isinstance(room, str) for room in plan["room_names"])
            or len(plan["room_names"]) != len(rooms) or set(plan["room_names"]) != set(rooms)
            or type(plan.get("max_retries")) is not int or plan["max_retries"] != 3
            or plan.get("continue_on_room_failure") is not True
            or not isinstance(plan.get("effective_from"), str)):
        raise ValueError("Invalid direct delivery recovery plan")
    effective = date.fromisoformat(plan["effective_from"])
    if effective.isoformat() != plan["effective_from"]:
        raise ValueError("Direct delivery effective date must be YYYY-MM-DD")
    return plan if day >= effective else None


def share_plan(config, day):
    if direct_plan(config, day):
        return None
    plan = config.get("share_delivery")
    if plan is None:
        return None
    if not isinstance(plan, dict):
        raise ValueError("Invalid KakaoTalk share delivery plan")
    rooms = configured_rooms(config)
    source, targets = plan.get("source_room"), plan.get("target_rooms")
    if (source not in rooms or not isinstance(targets, list) or not targets
            or any(not isinstance(room, str) for room in targets)
            or not isinstance(plan.get("effective_from"), str)):
        raise ValueError("Invalid KakaoTalk share delivery plan")
    effective = date.fromisoformat(plan["effective_from"])
    if (len(set(targets)) != len(targets)
            or set(targets) != set(rooms) - {source}
            or source in targets or effective.isoformat() != plan["effective_from"]):
        raise ValueError("Invalid KakaoTalk share delivery plan")
    return plan if day >= effective else None


def delivery_rooms(config, day):
    direct = direct_plan(config, day)
    if direct:
        return direct["room_names"]
    plan = share_plan(config, day)
    return [plan["source_room"], *plan["target_rooms"]] if plan else configured_rooms(config)


def share_receipt_path(state_root, folder):
    return state_root / f"{folder.name}-share.json"


def read_share_batch(folder, state_root):
    record = json.loads(share_receipt_path(state_root, folder).read_text(encoding="utf-8"))
    if (not isinstance(record, dict) or record.get("edition") != folder.name
            or record.get("image_sha256") != sha256(folder / "briefing.png")
            or record.get("ui_phase") not in {"share_selected", "send_requested"}
            or not isinstance(record.get("rooms"), list) or not record["rooms"]
            or any(not isinstance(room, str) or not room.strip() for room in record["rooms"])
            or not isinstance(record.get("source_room"), str) or not record["source_room"]
            or record["source_room"] in record["rooms"]
            or len(set(record["rooms"])) != len(record["rooms"])
            or not record.get("evidence")):
        raise ValueError("Invalid shared image attempt; inspect without resending")
    return record


def begin_share(folder, config, selected, observed_source, state_root, evidence, now):
    day = date.fromisoformat(folder.name.removesuffix("-am"))
    plan = share_plan(config, day)
    if not plan or observed_source != plan["source_room"] or not evidence.strip():
        raise ValueError("Exact source room and observed sharing selection are required")
    validate_bundle(folder, now)
    telegram = require_telegram_success(folder)
    previous = require_previous_rooms(folder, state_root, [plan["source_room"]])
    remaining = []
    for room in plan["target_rooms"]:
        require_room_active(config, room, now)
        path = receipt_path(state_root, folder, room)
        if path.exists():
            from morning_delivery_status import read_record
            record = read_record(path, folder, sha256(folder / "briefing.png"), room)
            if record["status"] not in {"sent", "skipped"}:
                raise ValueError("Unresolved recipient receipt; do not start a new share")
        else:
            remaining.append(room)
    if not remaining or selected != remaining:
        raise ValueError("Select exactly the remaining authorized rooms, excluding completed rooms")
    batch = {"edition": folder.name, "image_sha256": sha256(folder / "briefing.png"),
             "source_room": observed_source, "rooms": selected,
             "ui_phase": "share_selected", "evidence": evidence,
             "observed_at_kst": now.isoformat()}
    state_root.mkdir(parents=True, exist_ok=True)
    # The batch journal is written first; a partial write can never become a new send.
    with share_receipt_path(state_root, folder).open("x", encoding="utf-8") as handle:
        json.dump(batch, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    for room in selected:
        record = {"status": "pending", "room": room, "edition": folder.name,
                  "image_sha256": batch["image_sha256"], "delivery_method": "share",
                  "source_room": observed_source, "ui_phase": "share_selected",
                  "telegram_receipt": telegram, "previous_kakao_receipts": previous,
                  "attempted_at_kst": now.isoformat(), "ui_evidence": evidence}
        with receipt_path(state_root, folder, room).open("x", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
    return batch


def checkpoint_share(folder, selected, observed_source, state_root, evidence, now):
    batch = read_share_batch(folder, state_root)
    if (batch["ui_phase"] != "share_selected" or selected != batch["rooms"]
            or observed_source != batch["source_room"] or not evidence.strip()):
        raise ValueError("Share may already have occurred or selection changed; do not click again")
    records = []
    for room in selected:
        path = receipt_path(state_root, folder, room)
        record = json.loads(path.read_text(encoding="utf-8"))
        if (record.get("status") != "pending" or record.get("room") != room
                or record.get("edition") != folder.name
                or record.get("image_sha256") != batch["image_sha256"]
                or record.get("delivery_method") != "share"
                or record.get("source_room") != observed_source
                or record.get("ui_phase") != "share_selected"):
            raise ValueError("Unresolved shared recipient; inspect without sending")
        records.append((path, record))
    require_previous_rooms(folder, state_root, [observed_source])
    # Persist intent before per-room updates and before the UI confirmation click.
    batch.update(ui_phase="send_requested", evidence=evidence, observed_at_kst=now.isoformat())
    save_receipt(share_receipt_path(state_root, folder), batch)
    for path, record in records:
        record.update(ui_phase="send_requested", ui_evidence=evidence,
                      ui_observed_at_kst=now.isoformat())
        save_receipt(path, record)
    return batch


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


def require_previous_rooms(folder, state_root, previous_rooms, allow_deferred=False):
    image_hash = sha256(folder / "briefing.png")
    receipts = []
    if allow_deferred:
        from kakao_morning_recovery import read_recovery, valid_deferral, recovery_path
        from morning_delivery_status import read_record
        journal = read_recovery(folder, state_root)
    for room in previous_rooms:
        path = receipt_path(state_root, folder, room)
        if allow_deferred:
            record = read_record(path, folder, image_hash, room)
            if record["status"] in {"sent", "skipped"}:
                receipts.append(path.name)
                continue
            if valid_deferral(journal, room, path):
                receipts.append(recovery_path(state_root, folder).name)
                continue
            raise ValueError("Handle or explicitly defer the previous room first")
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


def begin(folder, room, observed_room, state_root, now, previous_rooms=(), allow_deferred=False):
    if observed_room != room:
        raise ValueError("Observed room does not match configured destination")
    validate_bundle(folder, now)
    telegram_receipt = require_telegram_success(folder)
    previous_receipts = require_previous_rooms(folder, state_root, previous_rooms, allow_deferred)
    if allow_deferred:
        from kakao_morning_recovery import read_recovery, valid_deferral
        if valid_deferral(read_recovery(folder, state_root), room,
                          receipt_path(state_root, folder, room)):
            raise ValueError("This room was deferred; do not start a new attempt")
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
    from kakao_morning_recovery import read_recovery, valid_deferral
    if observed_room != room or phase not in UI_PHASES or not evidence.strip():
        raise ValueError("Exact observed room, UI phase and evidence are required")
    path = receipt_path(state_root, folder, room)
    if valid_deferral(read_recovery(folder, state_root), room, path):
        raise ValueError("This room was deferred; do not resume its send steps")
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("delivery_method") == "share":
        raise ValueError("Use share-checkpoint for the shared selection")
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
    from morning_delivery_status import SKIP_REASONS, PRESEND_PHASES, SHARE_PRESEND_PHASES
    if reason not in SKIP_REASONS or not evidence.strip():
        raise ValueError("An allowed skip reason and actual observed evidence are required")
    validate_bundle(folder, now)
    telegram_receipt = require_telegram_success(folder)
    path = receipt_path(state_root, folder, room)
    image_hash = sha256(folder / "briefing.png")
    if share_receipt_path(state_root, folder).exists():
        batch = read_share_batch(folder, state_root)
        if room in batch["rooms"] and batch["ui_phase"] == "send_requested":
            raise ValueError("Share may already have occurred; cannot skip")
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        if (record.get("delivery_method") == "share"
                and read_share_batch(folder, state_root)["ui_phase"] == "send_requested"):
            raise ValueError("Share may already have occurred; cannot skip")
        if (record.get("status") != "pending" or record.get("room") != room
                or record.get("edition") != folder.name or record.get("image_sha256") != image_hash
                or record.get("ui_phase") not in PRESEND_PHASES + SHARE_PRESEND_PHASES):
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
    if record.get("delivery_method") == "share":
        batch = read_share_batch(folder, state_root)
        if room not in batch["rooms"] or batch["source_room"] != record.get("source_room"):
            raise ValueError("Shared recipient identity mismatch")
        if status == "sent" and batch["ui_phase"] != "send_requested":
            raise ValueError("Sharing selection is not proof of delivery")
    record.update(status=status, observed_at_kst=now.isoformat(), evidence=evidence)
    save_receipt(path, record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "begin", "checkpoint", "skip", "sent", "uncertain",
                                          "share-begin", "share-checkpoint", "retry", "defer"))
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--room", help="Exact configured room; required for mutations with multiple rooms")
    parser.add_argument("--observed-room")
    parser.add_argument("--evidence", default="")
    parser.add_argument("--phase")
    parser.add_argument("--reason")
    parser.add_argument("--operation")
    parser.add_argument("--share-room", action="append", default=[],
                        help="Repeat for the exact checked recipient rooms in sharing order")
    args = parser.parse_args()
    if args.share_room and args.action not in {"share-begin", "share-checkpoint"}:
        raise ValueError("--share-room is only for shared delivery actions")
    now = datetime.now(KST)
    folder = (args.bundle or ROOT / "output" / f"{now.date().isoformat()}-am").resolve()
    config = json.loads((ROOT / ".kakao_morning.json").read_text(encoding="utf-8"))
    day = date.fromisoformat(folder.name.removesuffix("-am"))
    rooms = delivery_rooms(config, day)
    plan = share_plan(config, day)
    direct = direct_plan(config, day)
    state_root = ROOT / ".morning_kakao_delivery"
    if args.action in {"share-begin", "share-checkpoint"}:
        if args.room is not None or not plan:
            raise ValueError("Use --share-room and an active shared delivery plan")
        if args.action == "share-begin":
            result = begin_share(folder, config, args.share_room, args.observed_room,
                                 state_root, args.evidence, now)
        else:
            if args.phase != "send_requested":
                raise ValueError("share-checkpoint requires --phase send_requested")
            validate_bundle(folder, now)
            result = checkpoint_share(folder, args.share_room, args.observed_room,
                                      state_root, args.evidence, now)
        from morning_delivery_status import reconcile
        reconcile(folder, ROOT)
        print(json.dumps(result, ensure_ascii=False))
        return
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
        if plan and room != plan["source_room"]:
            raise ValueError("Forward from the source image with share-begin; no per-room upload")
        result = begin(folder, room, args.observed_room, state_root, now,
                       previous_rooms=[r for r in rooms[:rooms.index(room)]
                                       if day >= date.fromisoformat(config.get("room_start_dates", {}).get(r, "1970-01-01"))],
                       allow_deferred=bool(direct))
    elif args.action in {"retry", "defer"}:
        if not direct:
            raise ValueError("Recovery continuation only applies to the dated direct delivery plan")
        require_room_active(config, room, now)
        from kakao_morning_recovery import record_retry, defer_room
        if args.action == "retry":
            result = record_retry(folder, state_root, room, args.operation, args.evidence, now)
        else:
            result = defer_room(folder, state_root, room, args.reason, args.evidence, now,
                                args.operation)
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
