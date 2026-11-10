"""Read the KST morning collection window; market-day checks remain separate."""

import argparse
import json
import math
from datetime import datetime, time
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
COLLECTION_START = time(7, 0)
FINAL_REFRESH_START = time(7, 30)
INFORMATION_CUTOFF = time(7, 35)


def morning_window(now):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Morning schedule needs a timezone-aware timestamp")
    now = now.astimezone(KST)
    start = datetime.combine(now.date(), COLLECTION_START, KST)
    refresh = datetime.combine(now.date(), FINAL_REFRESH_START, KST)
    cutoff = datetime.combine(now.date(), INFORMATION_CUTOFF, KST)
    phase = ("waiting_for_start" if now < start else
             "collecting" if now < refresh else
             "final_refresh" if now < cutoff else "ready_to_finalize")
    return {
        "date": now.date().isoformat(),
        "timezone": "Asia/Seoul",
        "collection_start": start.isoformat(),
        "final_refresh_start": refresh.isoformat(),
        "cutoff": cutoff.isoformat(),
        "night_futures_not_before": cutoff.isoformat(),
        "phase": phase,
        "wait_seconds": max(0, math.ceil((start - now).total_seconds())),
        "note": "07:00부터 사전 수집, 07:30~07:35 최종 갱신, 07:35부터 야간선물 조회 후 원고 확정.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", type=datetime.fromisoformat,
                        help="Read-only schedule check at an ISO timestamp with timezone")
    args = parser.parse_args()
    print(json.dumps(morning_window(args.now or datetime.now(KST)), ensure_ascii=False))


if __name__ == "__main__":
    main()
