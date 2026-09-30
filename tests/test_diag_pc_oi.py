#!/usr/bin/env python3
"""tools/diag_pc_oi.py 的純函式（2026-09-30 一次性診斷工具）——免 token、免網路。"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import diag_pc_oi as diag  # noqa: E402
import finmind  # noqa: E402
import sentiment  # noqa: E402

D = "2026-09-29"
FAKE_TOKEN = "FAKEtok_diag_1234567890abcdef"


def _r(cd, strike, cp, sess, oi, vol, oid="TXO"):
    return {"date": D, "option_id": oid, "contract_date": cd, "strike_price": strike,
            "call_put": cp, "trading_session": sess, "open_interest": oi, "volume": vol}


ROWS = [
    _r("202610", 22000, "put", "position", 1000, 300),
    _r("202610", 22000, "call", "position", 800, 200),
    _r("202610", 22000, "put", "after_market", 0, 50),
    _r("202610", 22000, "call", "after_market", 0, 70),
    _r("202610W1", 22000, "put", "position", 400, 90),
    _r("202610W1", 22000, "call", "position", 900, 110),
    _r("202611", 23000, "put", "position", 100, 10),
    _r("202611", 23000, "call", "position", 300, 20),
    _r("202610/202611", 23000, "call", "position", 5, 1),
]


def test_contract_kind():
    assert diag.contract_kind("202610") == "monthly"
    assert diag.contract_kind("202610W1") == "weekly"
    assert diag.contract_kind("202609w5") == "weekly"
    assert diag.contract_kind("202610/202611") == "other"
    assert diag.contract_kind(None) == "other"
    assert diag.contract_kind("") == "other"


def test_current_equals_pc_ratios():
    c = diag.candidates(ROWS)["current(position)"]
    ref = sentiment.pc_ratios(ROWS)
    assert c["pc"] == ref["pc_oi"]
    assert (c["put"], c["call"]) == (ref["put_oi"], ref["call_oi"])


def test_weekly_split():
    c = diag.candidates(ROWS)
    assert (c["position,weekly_only"]["put"], c["position,weekly_only"]["call"]) == (400, 900)
    ex = c["position,exclude_weekly"]
    assert (ex["put"], ex["call"]) == (1100, 1105)
    m = c["position,monthly_only(6碼)"]
    assert (m["put"], m["call"]) == (1100, 1100) and m["pc"] == 100.0
    assert "position,near_month_only(202610)" in c


def test_group_sums_and_dedup():
    g = diag.group_sums(ROWS, lambda r: (r["trading_session"], r["call_put"]))
    assert g[("position", "put")]["oi"] == 1500 and g[("position", "put")]["n"] == 3
    dup = ROWS + [dict(ROWS[0], open_interest=10)]
    assert diag.candidates(dup)["position,dedup_max"]["put"] == 1500


def test_matches_two_decimals():
    assert diag.matches(75.3, 75.30)
    assert not diag.matches(71.83, 75.30)
    assert not diag.matches(None, 75.30)


def test_report_day_marks_official_match():
    items = [{"Date": "20260929", "PutCallVolumeRatio%": "87.42", "PutCallOIRatio%": "100.00"}]
    buf = io.StringIO()
    with redirect_stdout(buf):
        diag.report_day(D, ROWS, items)
    out = buf.getvalue()
    assert "PutCallOIRatio%=100.0" in out
    assert any("monthly_only" in ln and "★" in ln for ln in out.splitlines())


def test_exception_masked(monkeypatch):
    monkeypatch.setenv("FINMIND_TOKEN", FAKE_TOKEN)
    monkeypatch.setattr(finmind, "_TOKEN", FAKE_TOKEN)

    def boom(d):
        raise RuntimeError(f"Fetch failed: https://x/api?dataset=TaiwanOptionDaily&token={FAKE_TOKEN}&data_id=TXO")

    def no_taifex():
        raise RuntimeError(f"taifex down token={FAKE_TOKEN}")
    monkeypatch.setattr(diag, "fetch_txo", boom)
    monkeypatch.setattr(diag, "fetch_taifex_raw", no_taifex)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = diag.main(["--dates", D])
    out = buf.getvalue()
    assert rc == 1
    assert FAKE_TOKEN not in out
    assert "token=***" in out
