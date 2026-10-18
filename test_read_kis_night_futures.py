import io
import json
import tempfile
import unittest
import zipfile
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import requests

import read_kis_night_futures as kis


def masters():
    # Official formats: pipe-delimited regular master, fixed-width night master.
    regular = "1|A01612|KR4A016C0004|F 202612|unused|unused|1|KOSPI200\n"
    night = "1" + "A01612".ljust(9) + "KR4A016C0004" + "F 202612".ljust(41) + "\n"
    return regular, night


def quote_payloads(raw_day="20261015"):
    metadata = {"hts_kor_isnm": "F 202612", "futs_shrn_iscd": "A01612"}
    night = {"stck_bsop_date": raw_day, "futs_prpr": "894.95"}
    regular = {"stck_bsop_date": raw_day, "futs_prpr": "861.30"}
    return {
        "night_quote": {"rt_cd": "0", "output1": dict(metadata,
            futs_last_tr_date="20261210", futs_prpr="894.95", futs_prdy_clpr="916.95")},
        "night_daily": {"rt_cd": "0", "output1": metadata, "output2": [night]},
        "regular_daily": {"rt_cd": "0", "output1": metadata, "output2": [regular]},
        "night_final_minute": {"rt_cd": "0", "output1": metadata,
            "output2": [dict(night, stck_cntg_hour="300000", cntg_vol="23")]},
        "regular_final_minute": {"rt_cd": "0", "output1": metadata,
            "output2": [dict(regular, stck_cntg_hour="154500")]},
    }


class Response:
    def __init__(self, payload=None, content=b"", status=200):
        self.status_code = status
        self.content = content
        self.payload = payload

    def json(self):
        return deepcopy(self.payload)


class Session:
    def __init__(self, payloads=None):
        self.payloads = payloads or quote_payloads()
        self.calls = []
        self.trust_env = True
        self.closed = False
        self.auth_fails = False

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if url.startswith(kis.MASTER_BASE):
            regular, night = masters()
            name = url.rsplit("/", 1)[1][:-4]
            content = regular if name == kis.MASTERS["regular"] else night
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr(name, content.encode("cp949"))
            return Response(content=buffer.getvalue())
        params = kwargs["params"]
        session = "night" if params["FID_COND_MRKT_DIV_CODE"] == "CM" else "regular"
        suffix = "quote" if url.endswith("/inquire-price") else "daily" if url.endswith("/inquire-daily-fuopchartprice") else "final_minute"
        return Response(self.payloads[f"{session}_{suffix}"])

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if self.auth_fails:
            raise requests.RequestException("app-secret-must-stay-private")
        return Response({"access_token": "issued-token-must-stay-private"})

    def close(self):
        self.closed = True


class KisNightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = self.root / ".env"
        self.env.write_text("KIS_APP_KEY=app-key-must-stay-private\nKIS_APP_SECRET=app-secret-must-stay-private\nKIS_USE_MOCK=false\n")
        self.cache = self.root / "context.json"
        self.cache.write_text(json.dumps({"environment": "live", "access_token": "cached-token-must-stay-private",
                                          "expires_at_epoch": 2000000000}))
        self.fixed = datetime(2026, 10, 16, 7, 35, 10, tzinfo=kis.KST)

    def collect(self, session=None, fixed=None, **options):
        fixed = fixed or self.fixed
        return kis.collect(fixed.date(), now=lambda: fixed, session=session or Session(),
                           env_file=self.env, token_cache=self.cache, **options)

    def test_live_format_normalizes_close_and_compares_same_contract_regular_close(self):
        session = Session()
        original_env, original_cache = self.env.read_bytes(), self.cache.read_bytes()
        report = self.collect(session)
        self.assertEqual(report["status"], "verified")
        data = report["data"]
        self.assertEqual(data["observed_at_kst"], "2026-10-16T06:00:00+09:00")
        self.assertEqual(data["previous_regular_close_at_kst"], "2026-10-15T15:45:00+09:00")
        self.assertEqual((data["night_close"], data["change_points"], data["change_percent"]), ("894.95", "33.65", "3.91"))
        self.assertEqual(report["scheduled_time_check"]["availability"], "verified_at_0735")
        self.assertEqual(len(session.calls), 7)  # Two masters, five quotations; all GET.
        self.assertTrue(all(method == "GET" for method, _, _ in session.calls))
        self.assertEqual(self.env.read_bytes(), original_env)
        self.assertEqual(self.cache.read_bytes(), original_cache)
        self.assertTrue(session.closed)
        self.assertFalse(session.trust_env)
        serialized = json.dumps(report)
        for secret in ["app-key-must-stay-private", "app-secret-must-stay-private", "cached-token-must-stay-private", "authorization"]:
            self.assertNotIn(secret, serialized)

    def test_after_open_availability_is_not_claimed_as_0735_verification(self):
        report = self.collect(fixed=self.fixed.replace(hour=9, minute=46))
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["scheduled_time_check"]["availability"], "verified_at_actual_time_only")
        self.assertFalse(report["scheduled_time_check"]["queried_in_0735_minute"])

    def test_monday_uses_friday_to_saturday_session(self):
        fixed = datetime(2026, 10, 19, 7, 35, 10, tzinfo=kis.KST)
        report = self.collect(Session(quote_payloads("20261016")), fixed)
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["data"]["session_start_at_kst"], "2026-10-16T18:00:00+09:00")
        self.assertEqual(report["data"]["observed_at_kst"], "2026-10-17T06:00:00+09:00")

    def test_stale_night_data_is_not_accepted(self):
        payloads = quote_payloads()
        payloads["night_daily"]["output2"][0]["stck_bsop_date"] = "20261014"
        report = self.collect(Session(payloads))
        self.assertEqual(report["status"], "unavailable")
        self.assertNotIn("data", report)

    def test_last_trade_before_final_auction_is_not_a_verified_close(self):
        payloads = quote_payloads()
        payloads["night_final_minute"]["output2"][0]["stck_cntg_hour"] = "294900"
        self.assertEqual(self.collect(Session(payloads))["reason"], "latest_completed_night_close_not_confirmed")

    def test_mismatching_contract_or_close_is_rejected(self):
        for field, value, expected in [
            ("futs_shrn_iscd", "OTHER", "history_contract_mismatch"),
            ("hts_kor_isnm", "F 202609", "history_contract_mismatch"),
        ]:
            with self.subTest(field=field):
                payloads = quote_payloads()
                payloads["regular_daily"]["output1"][field] = value
                self.assertEqual(self.collect(Session(payloads))["reason"], expected)
        payloads = quote_payloads()
        payloads["night_quote"]["output1"]["futs_prpr"] = "900.00"
        self.assertEqual(self.collect(Session(payloads))["reason"], "night_close_sources_disagree")

    def test_missing_comparison_is_not_filled_from_previous_night_field(self):
        payloads = quote_payloads()
        payloads["regular_daily"]["output2"] = []
        report = self.collect(Session(payloads))
        self.assertEqual(report["status"], "unavailable")
        self.assertNotIn("data", report)

    def test_master_match_and_roll_to_contract_available_in_both_sessions(self):
        regular, night = masters()
        regular += "1|A01609|KR4A01690000|F 202609|unused|unused|1|KOSPI200\n"
        self.assertEqual(kis.select_contract(regular, night, date(2026, 10, 16))["code"], "A01612")
        with self.assertRaisesRegex(kis.QuoteError, "no_matching"):
            kis.select_contract(regular, night.replace("KR4A016C0004", "KR4OTHER0000"), date(2026, 10, 16))

    def test_expired_cached_token_is_requested_once_in_memory_only(self):
        self.cache.write_text('{"environment":"live","expires_at_epoch":1}')
        old_cache = self.cache.read_bytes()
        session = Session()
        report = self.collect(session)
        self.assertEqual(report["status"], "verified")
        self.assertEqual(sum(method == "POST" for method, _, _ in session.calls), 1)
        self.assertEqual(self.cache.read_bytes(), old_cache)
        self.assertNotIn("issued-token-must-stay-private", json.dumps(report))

    def test_auth_failure_does_not_leak_or_retry(self):
        self.cache.unlink()
        session = Session()
        session.auth_fails = True
        report = self.collect(session)
        self.assertEqual(report["reason"], "authentication_request_failed")
        self.assertEqual(sum(method == "POST" for method, _, _ in session.calls), 1)
        self.assertNotIn("app-secret-must-stay-private", json.dumps(report))

    def test_cached_only_and_mock_credentials_never_issue_tokens(self):
        self.cache.unlink()
        session = Session()
        self.assertEqual(self.collect(session, cached_token_only=True)["reason"], "valid_existing_token_unavailable")
        self.assertTrue(all(method == "GET" for method, _, _ in session.calls))
        self.env.write_text("KIS_APP_KEY=x\nKIS_APP_SECRET=y\nKIS_USE_MOCK=true\n")
        self.assertEqual(self.collect()["reason"], "idea1_live_credentials_not_confirmed")

    def test_before_cutoff_and_closed_days_do_not_access_credentials_or_network(self):
        for fixed in [self.fixed.replace(minute=34), datetime(2026, 10, 17, 7, 35, tzinfo=kis.KST)]:
            with self.subTest(fixed=fixed):
                session = Session()
                self.assertEqual(self.collect(session, fixed)["status"], "unavailable")
                self.assertEqual(session.calls, [])

    def test_existing_snapshot_is_preserved_without_network_calls(self):
        bundle = self.root / "2026-10-16-am"
        bundle.mkdir()
        path = bundle / "kis-night-futures.json"
        saved = {"schema_version": 1, "date": "2026-10-16", "status": "unavailable", "reason": "first_failure"}
        path.write_text(json.dumps(saved))
        original = path.read_bytes()
        with patch.object(kis, "now_kst", return_value=self.fixed), patch.object(kis, "collect") as collect, patch("builtins.print"):
            self.assertEqual(kis.main(["--bundle", str(bundle)]), 2)
            collect.assert_not_called()
        self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
