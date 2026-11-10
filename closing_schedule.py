"""Read the KST closing collection window; market-day checks remain separate."""

import argparse
import json
import math
from datetime import datetime, time
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
COLLECTION_START = time(16, 0)
INFORMATION_CUTOFF = time(16, 20)


def closing_window(now):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Closing schedule needs a timezone-aware timestamp")
    now = now.astimezone(KST)
    start = datetime.combine(now.date(), COLLECTION_START, KST)
    cutoff = datetime.combine(now.date(), INFORMATION_CUTOFF, KST)
    phase = ("waiting_for_start" if now < start else
             "collecting" if now < cutoff else "ready_to_finalize")
    return {
        "date": now.date().isoformat(),
        "timezone": "Asia/Seoul",
        "collection_start": start.isoformat(),
        "cutoff": cutoff.isoformat(),
        "phase": phase,
        "wait_seconds": max(0, math.ceil((start - now).total_seconds())),
        "note": "16:00부터 수집·검증하고 16:15~16:20 최종 갱신 후 원고를 확정한다.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", type=datetime.fromisoformat,
                        help="Read-only schedule check at an ISO timestamp with timezone")
    args = parser.parse_args()
    print(json.dumps(closing_window(args.now or datetime.now(KST)), ensure_ascii=False))


if __name__ == "__main__":
    main()
