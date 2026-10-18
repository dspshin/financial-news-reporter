"""Collect KOSPI200 night closes with KIS; never read accounts or send orders.

Reuse stock idea1's live credentials and valid token without modifying its files.
If no valid token exists, request one once, keep it in memory, and never persist
credentials, tokens, HTTP headers, or authentication response bodies.
"""

import argparse
import io
import json
import re
import zipfile
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import dotenv_values

from morning_market_day import closure_reason


ROOT = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")
IDEA1_APP = Path.home() / "Library/Application Support/idea1/app"
ENV_FILE = IDEA1_APP / ".env"
TOKEN_CACHE = IDEA1_APP / ".runtime/helm-market-context-token.json"
BASE = "https://openapi.koreainvestment.com:9443"
MASTER_BASE = "https://new.real.download.dws.co.kr/common/master/"
MASTERS = {"regular": "fo_idx_code_mts.mst", "night": "fo_cme_code.mst"}
API = {
    "quote": ("/uapi/domestic-futureoption/v1/quotations/inquire-price", "FHMIF10000000"),
    "daily": ("/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice", "FHKIF03020100"),
    "minute": ("/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice", "FHKIF03020200"),
}
PUBLIC_FIELDS = {
    "hts_kor_isnm", "futs_shrn_iscd", "futs_last_tr_date", "futs_prpr",
    "futs_prdy_vrss", "futs_prdy_ctrt", "futs_prdy_clpr", "futs_sdpr",
    "stck_bsop_date", "stck_cntg_hour", "cntg_vol",
}


class QuoteError(Exception):
    """Only constant, credential-free reason codes may be exposed."""


def now_kst():
    return datetime.now(KST)


def positive_decimal(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise QuoteError("invalid_market_number") from None
    if not number.is_finite() or number <= 0:
        raise QuoteError("invalid_market_number")
    return number


def regular_contracts(text):
    result = {}
    for line in text.splitlines():
        row = [field.strip() for field in line.split("|")]
        if len(row) < 8 or row[0] != "1" or row[-1] != "KOSPI200":
            continue
        match = re.fullmatch(r"F\s+(20\d{2})(\d{2})", row[3])
        if match and 1 <= int(match[2]) <= 12:
            result[row[1]] = {
                "code": row[1], "isin": row[2], "name": row[3],
                "month": f"{match[1]}-{match[2]}",
            }
    return result


def select_contract(regular_text, night_text, edition_date):
    regular = regular_contracts(regular_text)
    matches = []
    for line in night_text.splitlines():
        if len(line) < 63 or line[0] != "1":
            continue
        code, isin, name = line[1:10].strip(), line[10:22].strip(), line[22:63].strip()
        contract = regular.get(code)
        if contract and contract["isin"] == isin and contract["name"] == name:
            if contract["month"] >= edition_date.strftime("%Y-%m"):
                matches.append(contract)
    if not matches:
        raise QuoteError("no_matching_standard_kospi200_contract")
    return min(matches, key=lambda contract: contract["month"])


def normalize_night_time(raw_date, raw_time):
    """KIS CM uses the opening date and hours 24~30 after midnight."""
    try:
        day = datetime.strptime(raw_date, "%Y%m%d").date()
        if not re.fullmatch(r"\d{6}", raw_time):
            raise ValueError
        hour, minute, second = int(raw_time[:2]), int(raw_time[2:4]), int(raw_time[4:])
        if not 18 <= hour <= 30 or not 0 <= minute < 60 or not 0 <= second < 60:
            raise ValueError
        if hour == 30 and (minute or second):
            raise ValueError
        return datetime.combine(day, time(), KST) + timedelta(hours=hour, minutes=minute, seconds=second)
    except (TypeError, ValueError):
        raise QuoteError("invalid_night_session_timestamp") from None


def previous_regular_day(edition_date):
    day = edition_date - timedelta(days=1)
    for _ in range(21):
        if not closure_reason(day):
            return day
        day -= timedelta(days=1)
    raise QuoteError("previous_regular_day_not_resolved")


def public_output(value):
    if isinstance(value, list):
        return [public_output(row) for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return {key: value[key] for key in PUBLIC_FIELDS if key in value}
    return None


class KisMarketClient:
    def __init__(self, report, *, session=None, env_file=ENV_FILE, token_cache=TOKEN_CACHE,
                 cached_token_only=False, now=now_kst):
        self.report = report
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.env_file = env_file
        self.token_cache = token_cache
        self.cached_token_only = cached_token_only
        self.now = now
        self._headers = None

    def close(self):
        self.session.close()

    def authenticate(self):
        # No environment mutation; only app credentials are used, never accounts.
        try:
            values = dotenv_values(self.env_file, interpolate=False)
            app_key, app_secret = values.get("KIS_APP_KEY"), values.get("KIS_APP_SECRET")
            live = str(values.get("KIS_USE_MOCK", "")).lower() in {"false", "0", "no", "n"}
        except (OSError, ValueError):
            raise QuoteError("idea1_credentials_unavailable") from None
        if not app_key or not app_secret or not live:
            raise QuoteError("idea1_live_credentials_not_confirmed")
        token = None
        try:
            cached = json.loads(self.token_cache.read_text(encoding="utf-8"))
            if (cached.get("environment") == "live"
                    and float(cached.get("expires_at_epoch", 0)) > self.now().timestamp() + 60):
                token = cached.get("access_token")
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        if isinstance(token, str) and token:
            self.report["authentication"] = {"method": "idea1_existing_token_read_only", "issuance_attempts": 0}
        else:
            if self.cached_token_only:
                raise QuoteError("valid_existing_token_unavailable")
            self.report["authentication"] = {"method": "one_in_memory_token_request", "issuance_attempts": 1}
            try:
                response = self.session.post(
                    BASE + "/oauth2/tokenP", json={"grant_type": "client_credentials",
                    "appkey": app_key, "appsecret": app_secret},
                    timeout=(5, 12), allow_redirects=False,
                )
                if response.status_code != 200:
                    raise QuoteError("authentication_request_failed")
                body = response.json()
                token = body.get("access_token")
                if not isinstance(token, str) or not token:
                    raise QuoteError("authentication_request_failed")
            except (requests.RequestException, ValueError, AttributeError):
                raise QuoteError("authentication_request_failed") from None
        self._headers = {"authorization": "Bearer " + token, "appkey": app_key,
                         "appsecret": app_secret, "custtype": "P"}

    def master(self, kind):
        name = MASTERS[kind]
        entry = {"label": kind + "_master", "method": "GET", "url": MASTER_BASE + name + ".zip",
                 "queried_at_kst": self.now().isoformat()}
        self.report["requests"].append(entry)
        try:
            response = self.session.get(entry["url"], timeout=(5, 12), allow_redirects=False)
            entry["http_status"] = response.status_code
            if response.status_code != 200 or len(response.content) > 4 * 1024 * 1024:
                raise QuoteError("master_download_failed")
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                if archive.getinfo(name).file_size > 8 * 1024 * 1024:
                    raise QuoteError("master_too_large")
                text = archive.read(name).decode("cp949")
            entry["status"] = "ok"
            return text
        except (requests.RequestException, ValueError, KeyError, zipfile.BadZipFile):
            raise QuoteError("master_download_failed") from None

    def market(self, label, kind, params):
        path, tr_id = API[kind]
        if self._headers is None:
            self.authenticate()
        entry = {"label": label, "method": "GET", "url": BASE + path,
                 "tr_id": tr_id, "params": params, "queried_at_kst": self.now().isoformat()}
        self.report["requests"].append(entry)
        if label == "night_quote":
            self.report["scheduled_time_check"] = timing_check(self.report["date"], entry["queried_at_kst"])
        try:
            response = self.session.get(entry["url"], headers=dict(self._headers, tr_id=tr_id),
                                        params=params, timeout=(5, 12), allow_redirects=False)
            entry["http_status"] = response.status_code
            if response.status_code != 200:
                raise QuoteError("market_http_error")
            body = response.json()
            # Never persist arbitrary response text/messages, headers, or auth errors.
            entry["rt_cd"] = "0" if body.get("rt_cd") == "0" else "error"
            if entry["rt_cd"] != "0":
                raise QuoteError("market_api_rejected")
            for key in ("output1", "output2"):
                if key in body:
                    entry[key] = public_output(body[key])
            return body
        except (requests.RequestException, ValueError, AttributeError):
            raise QuoteError("market_response_unavailable") from None


def timing_check(edition_date, queried_at):
    target = datetime.combine(date.fromisoformat(edition_date), time(7, 35), KST)
    actual = datetime.fromisoformat(queried_at).astimezone(KST)
    seconds = (actual - target).total_seconds()
    return {"target_at_kst": target.isoformat(), "actual_query_at_kst": actual.isoformat(),
            "seconds_after_target": round(seconds, 3), "queried_in_0735_minute": 0 <= seconds < 60,
            "availability": "not_verified"}


def daily_row(body, day):
    rows = body.get("output2")
    if not isinstance(rows, list):
        raise QuoteError("daily_rows_missing")
    found = [row for row in rows if isinstance(row, dict) and row.get("stck_bsop_date") == day.strftime("%Y%m%d")]
    if len(found) != 1:
        raise QuoteError("expected_session_missing_or_duplicate")
    return found[0]


def verify_quotes(contract, edition_date, quote, night_daily, regular_daily, night_minute, regular_minute):
    expected_day = previous_regular_day(edition_date)
    raw_date = expected_day.strftime("%Y%m%d")
    cutoff = datetime.combine(edition_date, time(7, 35), KST)
    night = daily_row(night_daily, expected_day)
    regular = daily_row(regular_daily, expected_day)
    try:
        last_night = night_minute["output2"][0]
        last_regular = regular_minute["output2"][0]
        snapshot = quote["output1"]
        expiry = datetime.strptime(snapshot["futs_last_tr_date"], "%Y%m%d").date()
        observed = normalize_night_time(last_night["stck_bsop_date"], last_night["stck_cntg_hour"])
    except (KeyError, IndexError, TypeError, ValueError):
        raise QuoteError("closing_rows_or_contract_metadata_missing") from None
    if (last_night.get("stck_bsop_date") != raw_date or last_night.get("stck_cntg_hour") != "300000"
            or observed > cutoff):
        raise QuoteError("latest_completed_night_close_not_confirmed")
    if snapshot.get("hts_kor_isnm", "").strip() != contract["name"]:
        raise QuoteError("quote_contract_mismatch")
    if expiry.strftime("%Y-%m") != contract["month"] or expiry < observed.date():
        raise QuoteError("contract_expired_or_month_mismatch")
    for body in (night_daily, regular_daily, night_minute, regular_minute):
        metadata = body.get("output1", {})
        if metadata.get("futs_shrn_iscd") != contract["code"] or metadata.get("hts_kor_isnm", "").strip() != contract["name"]:
            raise QuoteError("history_contract_mismatch")
    if (last_regular.get("stck_bsop_date") != raw_date
            or last_regular.get("stck_cntg_hour") != "154500"):
        raise QuoteError("regular_final_close_not_confirmed")
    night_price = positive_decimal(night.get("futs_prpr"))
    regular_price = positive_decimal(regular.get("futs_prpr"))
    if any(positive_decimal(row.get("futs_prpr")) != night_price for row in (last_night, snapshot)):
        raise QuoteError("night_close_sources_disagree")
    if positive_decimal(last_regular.get("futs_prpr")) != regular_price:
        raise QuoteError("regular_close_sources_disagree")
    change = night_price - regular_price
    pct = (night_price / regular_price - 1) * 100
    return {
        "product": "KRX 표준 KOSPI200 선물", **contract, "expiry": expiry.isoformat(),
        "price_type": "night_session_close", "unit": "index_points",
        "provider_raw_date": raw_date, "provider_raw_time": "300000",
        "provider_date_convention": "KIS CM 세션 시작일 + 자정 이후 24시간 가산",
        "krx_trading_date": None,
        "session_start_at_kst": datetime.combine(expected_day, time(18), KST).isoformat(),
        "observed_at_kst": observed.isoformat(), "published_at_kst": None,
        "night_close": str(night_price), "previous_regular_close": str(regular_price),
        "previous_regular_close_at_kst": datetime.combine(expected_day, time(15, 45), KST).isoformat(),
        "comparison": "동일 월물의 해당 야간 세션 직전 정규장 종가 대비",
        "change_points": str(change),
        "change_percent": str(pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "change_formula": "(night_close / previous_regular_close - 1) * 100",
        "direction": "up" if change > 0 else "down" if change < 0 else "flat",
        "cross_checks": ["regular_and_night_master_match", "CM_quote_daily_final_minute_match",
                         "F_daily_final_minute_match", "same_contract_comparison"],
        "note": "CM futs_prdy_clpr는 이전 야간 종가일 수 있어 비교 분모로 사용하지 않음",
    }


def collect(edition_date, *, client_factory=KisMarketClient, now=now_kst, **client_options):
    started = now().astimezone(KST)
    report = {"schema_version": 1, "source": "한국투자증권 OpenAPI", "date": edition_date.isoformat(),
              "cutoff_at_kst": datetime.combine(edition_date, time(7, 35), KST).isoformat(),
              "started_at_kst": started.isoformat(), "status": "unavailable", "requests": [],
              "scheduled_time_check": {"target_at_kst": datetime.combine(edition_date, time(7, 35), KST).isoformat(),
                                       "actual_query_at_kst": None, "queried_in_0735_minute": False,
                                       "availability": "not_verified"}}
    client = None
    try:
        if started.date() != edition_date or started < datetime.combine(edition_date, time(7, 35), KST):
            raise QuoteError("not_current_edition_or_before_0735")
        if closure_reason(edition_date):
            raise QuoteError("korean_market_closed")
        client = client_factory(report, now=now, **client_options)
        contract = select_contract(client.master("regular"), client.master("night"), edition_date)
        report["contract"] = contract
        common = {"FID_INPUT_ISCD": contract["code"]}
        quote = client.market("night_quote", "quote", dict(common, FID_COND_MRKT_DIV_CODE="CM"))
        daily_params = dict(common, FID_INPUT_DATE_1=(edition_date - timedelta(days=21)).strftime("%Y%m%d"),
                            FID_INPUT_DATE_2=edition_date.strftime("%Y%m%d"), FID_PERIOD_DIV_CODE="D")
        night_daily = client.market("night_daily", "daily", dict(daily_params, FID_COND_MRKT_DIV_CODE="CM"))
        regular_daily = client.market("regular_daily", "daily", dict(daily_params, FID_COND_MRKT_DIV_CODE="F"))
        minute_params = dict(common, FID_HOUR_CLS_CODE="60", FID_PW_DATA_INCU_YN="Y", FID_FAKE_TICK_INCU_YN="N")
        night_minute = client.market("night_final_minute", "minute", dict(minute_params,
            FID_COND_MRKT_DIV_CODE="CM", FID_INPUT_DATE_1=edition_date.strftime("%Y%m%d"), FID_INPUT_HOUR_1="300000"))
        regular_minute = client.market("regular_final_minute", "minute", dict(minute_params,
            FID_COND_MRKT_DIV_CODE="F", FID_INPUT_DATE_1=previous_regular_day(edition_date).strftime("%Y%m%d"),
            FID_INPUT_HOUR_1="154500"))
        report["data"] = verify_quotes(contract, edition_date, quote, night_daily, regular_daily, night_minute, regular_minute)
        report["status"] = "verified"
        report["scheduled_time_check"]["availability"] = (
            "verified_at_0735" if report["scheduled_time_check"]["queried_in_0735_minute"] else "verified_at_actual_time_only")
    except QuoteError as exc:
        report["reason"] = str(exc)
    except Exception:
        # Avoid traceback/request reprs containing credentials even on unexpected failures.
        report["reason"] = "unexpected_collection_error"
    finally:
        if client is not None:
            client.close()
    report["finished_at_kst"] = now().astimezone(KST).isoformat()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, help="output/YYYY-MM-DD-am (default: today's bundle)")
    parser.add_argument("--output", type=Path, help="Explicit diagnostic output file; does not change a published bundle")
    parser.add_argument("--cached-token-only", action="store_true", help="Do not issue a token during a manual diagnostic")
    args = parser.parse_args(argv)
    edition_date = now_kst().date()
    bundle = args.bundle or ROOT / "output" / f"{edition_date}-am"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-am", bundle.name) or bundle.name[:10] != edition_date.isoformat():
        parser.error("bundle must be today's YYYY-MM-DD-am")
    path = args.output or bundle / "kis-night-futures.json"
    # A resumed run keeps its initial observation (including failure and timing).
    if path.exists():
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if report.get("date") != edition_date.isoformat() or report.get("schema_version") != 1:
                raise ValueError
        except (OSError, ValueError, AttributeError):
            print(json.dumps({"status": "unavailable", "reason": "existing_snapshot_invalid_preserved"}))
            return 2
        reused = True
    else:
        report = collect(edition_date, cached_token_only=args.cached_token_only)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        reused = False
    print(json.dumps({"status": report["status"], "reason": report.get("reason"), "snapshot": str(path),
                      "reused": reused, "data": report.get("data"),
                      "scheduled_time_check": report.get("scheduled_time_check")}, ensure_ascii=False))
    return 0 if report["status"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
