"""Screen Korean market closures; require a fresh official-source check for open days."""

import argparse
import json
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import holidays

KST = ZoneInfo("Asia/Seoul")
KRX_CALENDAR_URL = "https://open.krx.co.kr/contents/MKD/01/0110/01100305/MKD01100305.jsp"


def closure_reason(day):
    if day.weekday() >= 5:
        return "주말"
    public = holidays.KR(years=day.year, observed=True, language="ko")
    if day in public:
        return str(public[day])
    if (day.month, day.day) == (5, 1):
        return "노동절(한국 증시 휴장)"
    year_end = date(day.year, 12, 31)
    while year_end.weekday() >= 5 or year_end in public:
        year_end -= timedelta(days=1)
    if day == year_end:
        return "한국 증시 연말 휴장"
    return None


def validate_market_day(check, now):
    day = now.astimezone(KST).date()
    reason = closure_reason(day)
    if reason:
        raise ValueError(f"Korean market closed: {reason}")
    if not isinstance(check, dict) or check.get("date") != day.isoformat() or check.get("status") != "open":
        raise ValueError("Today's Korean market opening must be verified before publishing")
    try:
        checked = datetime.fromisoformat(check["checked_at"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Market-day check needs an observation timestamp") from None
    if checked.tzinfo is None or checked.astimezone(KST).date() != day or checked > now:
        raise ValueError("Market-day observation must be from today, not the future")
    sources = check.get("sources", [])
    if not isinstance(sources, list) or not sources:
        raise ValueError("Market-day check needs official source links")
    for source in sources:
        if not isinstance(source, str):
            raise ValueError("Market-day source must be a URL")
        parsed = urlparse(source)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not (
            host == "krx.co.kr" or host.endswith(".krx.co.kr") or host.endswith(".go.kr")
        ):
            raise ValueError("Use KRX or Korean government sources for the market-day check")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, default=datetime.now(KST).date())
    args = parser.parse_args()
    reason = closure_reason(args.date)
    print(json.dumps({
        "date": args.date.isoformat(),
        "status": "closed" if reason else "verification_required",
        "reason": reason,
        "calendar_url": KRX_CALENDAR_URL,
        "note": "임시공휴일·특별휴장·법정공휴일 변경은 당일 공식 자료로 추가 확인한다.",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
