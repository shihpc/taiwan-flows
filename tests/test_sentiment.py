#!/usr/bin/env python3
"""市場情緒指標（2026-09-29，規格 docs/sentiment-tab.md §0／§2／N2）——免 token、免網路。

fixture 用 §0 的 2026-09-24 Hetzner 實測數字（與期交所官方逐位相同）：
  VIX 當日最後一筆 13:45:00＝23.12
  TXO position OI：put 59,603 / call 69,848 → 85.33%
  TXO 成交量（position＋after_market）：put 83,032＋42,942 / call 54,472＋49,543 → 121.11%
  MTX 三大法人多 3,304、空 10,719 → 散戶淨 +7,415 口
小台全市場未平倉的實數當時未取得，下方 MTX 列是**構造值**（只驗算式與兩種口徑的分流）。
"""
from __future__ import annotations

import io
import json
import logging
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import finmind  # noqa: E402
import run_daily  # noqa: E402
import sentiment  # noqa: E402

D = "2026-09-24"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sentiment_sample.json"
FAKE_TOKEN = "FAKEtok_9f8e7d6c5b4a3210zyxw"   # 假 token：只拿來驗遮罩，任何輸出都不得含它

VIX_ROWS = [  # 刻意亂序＋一筆他日列＋一筆壞值
    {"date": D, "time": "13:44:00", "vix": 23.5},
    {"date": D, "time": "13:45:00", "vix": 23.12},
    {"date": D, "time": "08:45:00", "vix": 21.0},
    {"date": D, "time": "13:46:00", "vix": None},
    {"date": "2026-09-23", "time": "13:45:00", "vix": 99.0},
]
TXO_ROWS = [
    # position：OI 與成交量都算
    {"date": D, "call_put": "put", "trading_session": "position", "open_interest": 30000, "volume": 40000},
    {"date": D, "call_put": "put", "trading_session": "position", "open_interest": 29603, "volume": 43032},
    {"date": D, "call_put": "call", "trading_session": "position", "open_interest": 40000, "volume": 30000},
    {"date": D, "call_put": "call", "trading_session": "position", "open_interest": 29848, "volume": 24472},
    # after_market：只算成交量（OI 即使非 0 也不得計入）
    {"date": D, "call_put": "put", "trading_session": "after_market", "open_interest": 0, "volume": 20000},
    {"date": D, "call_put": "put", "trading_session": "after_market", "open_interest": 777, "volume": 22942},
    {"date": D, "call_put": "call", "trading_session": "after_market", "open_interest": 0, "volume": 25000},
    {"date": D, "call_put": "call", "trading_session": "after_market", "open_interest": 0, "volume": 24543},
]
INST_ROWS = [
    {"date": D, "futures_id": "MTX", "institutional_investors": "自營商",
     "long_open_interest_balance_volume": 1200, "short_open_interest_balance_volume": 2000},
    {"date": D, "futures_id": "MTX", "institutional_investors": "投信",
     "long_open_interest_balance_volume": 104, "short_open_interest_balance_volume": 719},
    {"date": D, "futures_id": "MTX", "institutional_investors": "外資",
     "long_open_interest_balance_volume": 2000, "short_open_interest_balance_volume": 8000},
]
MTX_ROWS = [  # 構造值：月 30,000＋2,000、週 5,000、價差 0；after_market 的 OI 不得計入
    {"date": D, "futures_id": "MTX", "contract_date": "202610", "trading_session": "position", "open_interest": 30000},
    {"date": D, "futures_id": "MTX", "contract_date": "202611", "trading_session": "position", "open_interest": 2000},
    {"date": D, "futures_id": "MTX", "contract_date": "202609W5", "trading_session": "position", "open_interest": 5000},
    {"date": D, "futures_id": "MTX", "contract_date": "202610/202611", "trading_session": "position", "open_interest": 0},
    {"date": D, "futures_id": "MTX", "contract_date": "202610", "trading_session": "after_market", "open_interest": 999},
]
TAIFEX_ITEMS = [  # 期交所 OpenAPI PutCallRatio 的欄名形狀
    {"Date": "20260924", "PutVolume": "125974", "CallVolume": "104015", "PutCallVolumeRatio%": "121.11",
     "PutOI": "59603", "CallOI": "69848", "PutCallOIRatio%": "85.33"},
    {"Date": "20260923", "PutVolume": "1", "CallVolume": "1", "PutCallVolumeRatio%": "100.00",
     "PutOI": "1", "CallOI": "1", "PutCallOIRatio%": "100.00"},
]
DATASETS = {"TaiwanOptionVix": VIX_ROWS, "TaiwanOptionDaily": TXO_ROWS,
            "TaiwanFuturesDaily": MTX_ROWS, "TaiwanFuturesInstitutionalInvestors": INST_ROWS}
ROW_KEYS = ["date", "vix", "pc_oi", "pc_vol", "put_oi", "call_oi", "put_vol", "call_vol",
            "mtx_oi", "mtx_oi_monthly_only", "inst_long", "inst_short", "retail_net", "retail_ratio", "cv"]


def fixture_fetch(calls=None, empty=(), fail=()):
    """假 fetch：回 DATASETS 的該日列（日期換成請求的那天）；empty 內的資料集回 []、fail 內回 None。"""
    def f(dataset, **params):
        d = params["start_date"]
        if calls is not None:
            calls.append((dataset, d))
        if dataset in fail or d in fail:
            return None
        if dataset in empty:
            return []
        return [dict(r, date=d) for r in DATASETS[dataset] if r["date"] == D]
    return f


# ---------- 純函式：§0 口徑逐位 ----------

def test_vix_close_takes_last_minute():
    v = sentiment.vix_close([r for r in VIX_ROWS if r["date"] == D])
    assert v == {"vix": 23.12, "time": "13:45:00", "n": 3}


def test_pc_ratios_exact_digits():
    pc = sentiment.pc_ratios(TXO_ROWS)
    assert pc["pc_oi"] == 85.33 and pc["pc_vol"] == 121.11
    assert (pc["put_oi"], pc["call_oi"]) == (59603, 69848)          # after_market 的 777 不得計入
    assert (pc["put_vol"], pc["call_vol"]) == (125974, 104015)
    assert pc["n"] == 8


def test_ratio_uses_js_half_up_not_bankers():
    """1/32 ×100 = 3.125（二進位可精確表示）：JS Math.round → 3.13；Python round → 3.12。"""
    rows = [{"call_put": "put", "trading_session": "position", "open_interest": 1, "volume": 1},
            {"call_put": "call", "trading_session": "position", "open_interest": 32, "volume": 32}]
    assert round(3.125, 2) == 3.12            # 前提：Python 內建是 banker's
    assert sentiment.pc_ratios(rows)["pc_oi"] == 3.13


def test_retail_net_and_two_oi_bases():
    rt = sentiment.retail_ratio(MTX_ROWS, INST_ROWS)
    assert (rt["inst_long"], rt["inst_short"], rt["retail_net"]) == (3304, 10719, 7415)
    assert rt["mtx_oi_all"] == 37000                 # 全部契約（含週契約 202609W5）
    assert rt["mtx_oi_monthly_only"] == 32000        # 排除含 W 的週契約
    assert rt["mtx_oi"] == 37000                     # 主值口徑預設 all
    assert rt["retail_ratio"] == 20.04               # 7415 / 37000 = 20.0405…
    alt = sentiment.retail_ratio(MTX_ROWS, INST_ROWS, main="monthly")
    assert alt["mtx_oi"] == 32000 and alt["retail_ratio"] == 23.17   # 7415 / 32000 = 23.171875


def test_retail_ratio_negative_net():
    inst = [{"long_open_interest_balance_volume": 500, "short_open_interest_balance_volume": 100}]
    mtx = [{"contract_date": "202610", "trading_session": "position", "open_interest": 1000}]
    rt = sentiment.retail_ratio(mtx, inst)
    assert rt["retail_net"] == -400 and rt["retail_ratio"] == -40.0


def test_zero_rows_give_null():
    assert sentiment.vix_close([])["vix"] is None
    pc = sentiment.pc_ratios([])
    assert all(pc[k] is None for k in ("pc_oi", "pc_vol", "put_oi", "call_oi", "put_vol", "call_vol"))
    rt = sentiment.retail_ratio([], [])
    assert all(rt[k] is None for k in ("mtx_oi", "mtx_oi_monthly_only", "inst_long", "inst_short",
                                        "retail_net", "retail_ratio"))
    rt2 = sentiment.retail_ratio([], INST_ROWS)       # 有法人、無全市場 → 淨部位有、比值 null
    assert rt2["retail_net"] == 7415 and rt2["retail_ratio"] is None and rt2["mtx_oi"] is None


# ---------- compute_day ----------

def test_compute_day_matches_spec_row():
    calls = []
    row = sentiment.compute_day(D, fixture_fetch(calls))
    assert list(row) == ROW_KEYS
    assert row == {"date": D, "vix": 23.12, "pc_oi": 85.33, "pc_vol": 121.11,
                   "put_oi": 59603, "call_oi": 69848, "put_vol": 125974, "call_vol": 104015,
                   "mtx_oi": 37000, "mtx_oi_monthly_only": 32000, "inst_long": 3304, "inst_short": 10719,
                   "retail_net": 7415, "retail_ratio": 20.04, "cv": 2}
    assert sorted(c[0] for c in calls) == sorted(DATASETS)   # 恰 4 個請求


def test_compute_day_single_dataset_empty_only_nulls_that_field():
    row = sentiment.compute_day(D, fixture_fetch(empty=("TaiwanOptionVix",)))
    assert row["vix"] is None and row["pc_oi"] == 85.33 and row["retail_net"] == 7415


def test_compute_day_request_failure_raises():
    with pytest.raises(sentiment.SentimentFetchError):
        sentiment.compute_day(D, fixture_fetch(fail=("TaiwanOptionDaily",)))


# ---------- update：升序補洞、上限、同日覆寫 ----------

def _cal(n, start="2026-03-02"):
    from datetime import date, timedelta
    d = date.fromisoformat(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_update_ascending_with_cap_and_fills_gaps(tmp_path):
    cal = ["2026-02-26", "2026-02-27"] + _cal(30)      # START 之前的兩天不得被算
    p = tmp_path / "sentiment.json"
    calls = []
    out = sentiment.update(cal, fetch=fixture_fetch(calls), taifex_fetch=lambda: [], path=p, max_days=5)
    days = [d for ds, d in calls if ds == "TaiwanOptionVix"]
    assert days == sorted(days)                         # 逐日升序
    assert len(days) == 5                               # 每班上限
    assert days[-1] == cal[-1]                          # 最新交易日一定在內
    assert days[:4] == cal[2:6]                         # 其餘由最舊缺漏補起
    assert all(d >= sentiment.SENTIMENT_START for d in days)
    dates = [r["date"] for r in out["rows"]]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)
    assert json.loads(p.read_text(encoding="utf-8")) == out

    # 下一班：接著補（不重算已有的舊日），最新日重算；幾班後補齊、無重複
    for _ in range(10):
        out = sentiment.update(cal, fetch=fixture_fetch(), taifex_fetch=lambda: [], path=p, max_days=5)
    dates = [r["date"] for r in out["rows"]]
    assert dates == cal[2:]


def test_update_default_cap_is_20(tmp_path):
    calls = []
    sentiment.update(_cal(60), fetch=fixture_fetch(calls), taifex_fetch=lambda: [], path=tmp_path / "s.json")
    assert len([1 for ds, _ in calls if ds == "TaiwanOptionVix"]) == sentiment.SENTIMENT_MAX_BACKFILL == 20


def test_update_middle_gap_filled(tmp_path):
    cal = _cal(10)
    p = tmp_path / "s.json"
    rows = [dict(sentiment.compute_day(d, fixture_fetch())) for d in cal if d != cal[4]]
    p.write_text(json.dumps({"schema": 1, "start": sentiment.SENTIMENT_START, "rows": rows}), encoding="utf-8")
    calls = []
    out = sentiment.update(cal, fetch=fixture_fetch(calls), taifex_fetch=lambda: [], path=p)
    assert sorted({d for _, d in calls}) == [cal[4], cal[-1]]   # 缺口＋最新日
    assert [r["date"] for r in out["rows"]] == cal


def test_update_same_day_overwrites(tmp_path):
    p = tmp_path / "s.json"
    old = dict(sentiment.compute_day(D, fixture_fetch()), vix=1.0, pc_oi=None)
    p.write_text(json.dumps({"schema": 1, "start": sentiment.SENTIMENT_START, "rows": [old]}), encoding="utf-8")
    out = sentiment.update([D], fetch=fixture_fetch(), taifex_fetch=lambda: [], path=p, dates=[D])
    assert len(out["rows"]) == 1
    assert out["rows"][0]["vix"] == 23.12 and out["rows"][0]["pc_oi"] == 85.33


def test_update_failure_skips_day_and_raises_after_writing(tmp_path):
    cal = _cal(5)
    p = tmp_path / "s.json"
    with pytest.raises(sentiment.SentimentFetchError) as ex:
        sentiment.update(cal, fetch=fixture_fetch(fail=(cal[1],)), taifex_fetch=lambda: [], path=p)
    out = json.loads(p.read_text(encoding="utf-8"))
    assert [r["date"] for r in out["rows"]] == [cal[0], cal[2], cal[3], cal[4]]   # 失敗日留缺、不寫 null
    assert cal[1] in str(ex.value)


def test_update_gives_up_after_consecutive_failures(tmp_path):
    calls = []
    with pytest.raises(sentiment.SentimentFetchError):
        sentiment.update(_cal(10), fetch=fixture_fetch(calls, fail=("TaiwanOptionVix",)),
                         taifex_fetch=lambda: [], path=tmp_path / "s.json")
    assert len(calls) == sentiment.SENTIMENT_MAX_CONSEC_FAIL


def _fake_clock(step):
    """假牆鐘：每呼叫一次前進 step 秒（update 進場取 t0 一次、每算完一天取一次）。"""
    t = {"now": -step}
    def c():
        t["now"] += step
        return t["now"]
    return c


def test_update_budget_exhausted_stops_writes_and_does_not_raise(tmp_path, caplog):
    cal = _cal(10)
    p = tmp_path / "s.json"
    calls = []
    with caplog.at_level(logging.WARNING, logger=sentiment.logger.name):
        out = sentiment.update(cal, fetch=fixture_fetch(calls), taifex_fetch=lambda: [], path=p,
                               budget_sec=600, clock=_fake_clock(250))   # 250/500/750 → 第 3 天後超標
    days = [d for ds, d in calls if ds == "TaiwanOptionVix"]
    assert days == cal[:3]                                               # 停止本班，不再發請求
    assert [r["date"] for r in out["rows"]] == cal[:3]
    assert json.loads(p.read_text(encoding="utf-8")) == out              # 已算的有寫入
    assert "剩餘 7 天" in caplog.text and "留待下班" in caplog.text
    # 下一班（預算充裕）自然補回剩餘天數
    out = sentiment.update(cal, fetch=fixture_fetch(), taifex_fetch=lambda: [], path=p,
                           clock=_fake_clock(0))
    assert [r["date"] for r in out["rows"]] == cal


def test_update_budget_default_and_last_day_not_logged(tmp_path, caplog):
    assert sentiment.SENTIMENT_BUDGET_SEC == 300
    cal = _cal(3)
    with caplog.at_level(logging.WARNING, logger=sentiment.logger.name):
        out = sentiment.update(cal, fetch=fixture_fetch(), taifex_fetch=lambda: [],
                               path=tmp_path / "s.json", clock=_fake_clock(10_000))
    assert [r["date"] for r in out["rows"]] == cal[:1]                   # 預設預算同樣生效
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=sentiment.logger.name):
        sentiment.update(cal[:1], fetch=fixture_fetch(), taifex_fetch=lambda: [],
                         path=tmp_path / "t.json", clock=_fake_clock(10_000))
    assert "留待下班" not in caplog.text                                 # 最後一天算完才超標＝沒有剩餘，不喊停


def test_output_schema_exact(tmp_path):
    out = sentiment.update([D], fetch=fixture_fetch(), taifex_fetch=lambda: TAIFEX_ITEMS,
                           path=tmp_path / "s.json")
    assert list(out) == ["schema", "generated_at", "start", "rows", "check"]
    assert out["schema"] == 1 and out["start"] == "2026-03-02"
    assert out["generated_at"].endswith("+08:00")
    assert list(out["rows"][0]) == ROW_KEYS
    assert out["check"] == {"taifex_pc": {"date": D, "pc_oi": 85.33, "pc_vol": 121.11, "match": True}}


def test_taifex_unreachable_does_not_block(tmp_path):
    def boom():
        raise ConnectionError(f"https://openapi.taifex.com.tw/v1/PutCallRatio?token={FAKE_TOKEN}")
    out = sentiment.update([D], fetch=fixture_fetch(), taifex_fetch=boom, path=tmp_path / "s.json")
    c = out["check"]["taifex_pc"]
    assert c["match"] is None and c["date"] is None and c["note"].startswith("unreachable")
    assert out["rows"][0]["pc_oi"] == 85.33


def test_taifex_mismatch():
    items = [dict(TAIFEX_ITEMS[0], **{"PutCallOIRatio%": "85.34"})]
    rows = [sentiment.compute_day(D, fixture_fetch())]
    assert sentiment.taifex_check(rows, lambda: items)["match"] is False


def test_sample_fixture_matches_computation():
    """tests/fixtures/sentiment_sample.json（供 postmkt 前端測試對照）與本模組算出的一致。"""
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert list(doc) == ["schema", "generated_at", "start", "rows", "check"]
    row = next(r for r in doc["rows"] if r["date"] == D)
    assert row == sentiment.compute_day(D, fixture_fetch())


# ---------- M3：token 遮罩 ----------

def test_mask_secret_both_layers(monkeypatch):
    monkeypatch.setattr(finmind, "_TOKEN", FAKE_TOKEN)
    s = finmind.mask_secret(f"GET https://api.finmindtrade.com/api/v4/data?dataset=X&token={FAKE_TOKEN}&a=1 raw {FAKE_TOKEN}")
    assert FAKE_TOKEN not in s and "token=***&a=1" in s
    monkeypatch.setattr(finmind, "_TOKEN", None)
    monkeypatch.delenv("FINMIND_TOKEN", raising=False)
    s2 = finmind.mask_secret("x?token=abc%2Bdef ok")      # token 未載入時仍遮 token= 後綴
    assert "abc" not in s2 and s2 == "x?token=*** ok"


def test_fm_get_exception_log_masked(monkeypatch, caplog):
    monkeypatch.setattr(finmind, "_TOKEN", FAKE_TOKEN)
    monkeypatch.setattr(finmind.time, "sleep", lambda s: None)

    def bad_get(url, params=None, timeout=None):
        raise ConnectionError(f"Max retries exceeded with url: /api/v4/data?dataset=X&token={params['token']}")
    monkeypatch.setattr(finmind.requests, "get", bad_get)
    with caplog.at_level(logging.DEBUG):
        assert finmind.fm_get("TaiwanOptionVix", retries=2, backoff=0) is None
    assert caplog.text and FAKE_TOKEN not in caplog.text


def test_update_error_and_logs_do_not_leak_token(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(finmind, "_TOKEN", FAKE_TOKEN)

    def leaky(dataset, **params):
        raise ConnectionError(f"https://api.finmindtrade.com/api/v4/data?dataset={dataset}&token={FAKE_TOKEN}")
    p = tmp_path / "s.json"
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(sentiment.SentimentFetchError) as ex:
            sentiment.update(_cal(3), fetch=leaky, taifex_fetch=lambda: [], path=p)
    assert FAKE_TOKEN not in str(ex.value)
    assert FAKE_TOKEN not in caplog.text
    assert FAKE_TOKEN not in p.read_text(encoding="utf-8")


# ---------- M2：run_daily 接點 ----------

@pytest.fixture
def rd_tmp(tmp_path, monkeypatch):
    (tmp_path / "daily").mkdir()
    (tmp_path / "meta.json").write_text(json.dumps({"calendar": [D]}), encoding="utf-8")
    monkeypatch.setattr(run_daily, "DATA", tmp_path)
    monkeypatch.setattr(run_daily, "STATUS_PATH", tmp_path / "status.json")

    def fake_run_date(d):
        (tmp_path / "daily" / f"{d.replace('-', '')}.json").write_text('{"rows":[]}', encoding="utf-8")
        return True
    order = []
    monkeypatch.setattr(run_daily, "run_date", fake_run_date)
    monkeypatch.setattr(run_daily, "rebuild_products", lambda: order.append("rebuild"))
    return tmp_path, order


def test_run_daily_sentiment_exception_does_not_break(rd_tmp, monkeypatch):
    tmp, order = rd_tmp
    monkeypatch.setattr(finmind, "_TOKEN", FAKE_TOKEN)

    def boom(cal, **kw):
        order.append("sentiment")
        raise RuntimeError(f"url ...&token={FAKE_TOKEN}&x=1 / {FAKE_TOKEN}")
    monkeypatch.setattr(sentiment, "update", boom)
    buf = io.StringIO()
    with redirect_stdout(buf):
        run_daily.main(["--date", D])                  # 不丟 SystemExit＝exit 0 語意不變
    assert order == ["rebuild", "sentiment"]           # 既有產出完成之後才呼叫
    st = json.loads((tmp / "status.json").read_text(encoding="utf-8"))
    assert st["status"] == "ok" and st["healthcheck"]["severity"] == "pending"
    assert "sentiment_error" in st and FAKE_TOKEN not in st["sentiment_error"]
    assert "sentiment" not in st["sources"]
    out = buf.getvalue()
    assert "::warning::" in out and FAKE_TOKEN not in out

    # verify_daily 那類「只更新健檢」的寫入不會洗掉 sentiment_error
    run_daily.write_status(D, "ok", "", healthcheck={"severity": "ok"}, attempt=False)
    assert "sentiment_error" in json.loads((tmp / "status.json").read_text(encoding="utf-8"))


def test_run_daily_sentiment_offline_default_is_caught(rd_tmp):
    """不 monkeypatch update：conftest 讓預設抓取立即失敗，整條真實路徑也不得拖垮 run_daily。"""
    tmp, _ = rd_tmp
    run_daily.main(["--date", D])
    st = json.loads((tmp / "status.json").read_text(encoding="utf-8"))
    assert st["status"] == "ok" and "sentiment_error" in st


def test_run_daily_sentiment_success_sets_source(rd_tmp, monkeypatch):
    tmp, _ = rd_tmp
    (tmp / "status.json").write_text(json.dumps({"sentiment_error": "舊錯誤"}), encoding="utf-8")
    monkeypatch.setattr(sentiment, "_default_fetch", fixture_fetch())
    monkeypatch.setattr(sentiment, "_default_taifex", lambda: TAIFEX_ITEMS)
    run_daily.main(["--date", D])
    st = json.loads((tmp / "status.json").read_text(encoding="utf-8"))
    assert st["status"] == "ok"
    assert st["sources"]["sentiment"] == D
    assert "sentiment_error" not in st                 # 成功即清除
    doc = json.loads((tmp / "sentiment.json").read_text(encoding="utf-8"))
    assert doc["rows"][-1]["pc_oi"] == 85.33 and doc["check"]["taifex_pc"]["match"] is True


def test_run_daily_non_ok_paths_do_not_call_sentiment(rd_tmp, monkeypatch):
    tmp, _ = rd_tmp
    called = []
    monkeypatch.setattr(sentiment, "update", lambda *a, **k: called.append(1))
    monkeypatch.setattr(run_daily, "run_date", lambda d: False)
    run_daily.main(["--date", "2026-09-26"])           # 週六 → no_data、exit 0
    assert called == []
    assert json.loads((tmp / "status.json").read_text(encoding="utf-8"))["status"] == "no_data"


def test_run_daily_uses_default_budget():
    """run_daily 呼叫 update() 時不得自帶 budget_sec（要吃模組預設值，否則預算調整不會生效）。以 ast 判斷，註解不影響。"""
    import ast
    from pathlib import Path as _P
    tree = ast.parse((_P(__file__).resolve().parents[1] / "src" / "run_daily.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "update" and isinstance(n.func.value, ast.Name) and n.func.value.id == "sentiment"]
    assert calls, "run_daily 找不到 sentiment.update( 呼叫"
    for c in calls:
        assert all(k.arg is not None for k in c.keywords), "不得以 ** 展開傳參"
        assert all(k.arg != "budget_sec" for k in c.keywords)


# ---------- §0b：未平倉排除當日到期契約（2026-09-30） ----------
#
# 逐契約數字＝run 36678263476（tools/diag_pc_oi.py）印出的 09-24／09-29 TXO position 逐契約 OI 加總
# （每契約壓成 put／call 各一列；成交量不在此驗，填 0）。官方：09-24 59603/69848＝85.33%、
# 09-29 47553/63148＝75.30%。

import twse_holidays  # noqa: E402

HOL = twse_holidays.parse(json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "twse_holidays_2026.json").read_text(encoding="utf-8")))

TXO_BY_CONTRACT = {
    "2026-09-24": {"202609F4": (25184, 31654), "202609W5": (9722, 8237), "202610": (17039, 19521),
                   "202610F1": (1088, 683), "202610W1": (1373, 323), "202611": (1467, 1204),
                   "202612": (2641, 4247), "202703": (747, 3707), "202706": (342, 272)},
    "2026-09-29": {"202609F4": (44870, 65523), "202609W5": (18556, 27213), "202610": (17923, 20018),
                   "202610F1": (2771, 5389), "202610F2": (643, 140), "202610W1": (2287, 745),
                   "202611": (1624, 1493), "202612": (2664, 4188), "202703": (752, 3679),
                   "202706": (333, 283)},
}


def _txo_rows(d):
    out = []
    for cd, (p, c) in TXO_BY_CONTRACT[d].items():
        out.append({"date": d, "contract_date": cd, "call_put": "put", "trading_session": "position",
                    "open_interest": p, "volume": 0})
        out.append({"date": d, "contract_date": cd, "call_put": "call", "trading_session": "position",
                    "open_interest": c, "volume": 0})
    return out


def _txo_fetch(dataset, **params):
    d = params["start_date"]
    return _txo_rows(d) if dataset == "TaiwanOptionDaily" else []


def test_contract_expiry_rules():
    assert sentiment.contract_expiry("202609W5", HOL) == "2026-09-30"
    assert sentiment.contract_expiry("202609F4", HOL) == "2026-09-29"   # 09-25 中秋、26/27 週末、28 教師節 → 順延
    assert sentiment.contract_expiry("202610F2", HOL) == "2026-10-12"   # 10-09 休市 → 10-10 週六 … → 10-12
    assert sentiment.contract_expiry("202609", HOL) == "2026-09-16"     # 6 碼＝第 3 個週三
    assert sentiment.contract_expiry("202610", HOL) == "2026-10-21"
    assert sentiment.contract_expiry("202610/202611", HOL) is None      # 價差：無法解析
    for bad in (None, "", "2026", "202613", "202609X1", "202609W6", "202602W5"):  # 2026-02 只有 4 個週三
        assert sentiment.contract_expiry(bad, HOL) is None, bad
    # fail-open：行事曆未載入只排週末
    assert sentiment.contract_expiry("202609F4", None) == "2026-09-25"
    assert sentiment.contract_expiry("202610F2", None) == "2026-10-09"
    assert sentiment.contract_expiry("202610F2", HOL) != sentiment.contract_expiry("202610F2", None)


def test_expiring_contracts_and_unparsed():
    rows = _txo_rows("2026-09-29") + [{"contract_date": "202610/202611"}, {"contract_date": None}]
    ex, bad = sentiment.expiring_contracts(rows, "2026-09-29", HOL)
    assert ex == {"202609F4"} and bad == ["202610/202611", "<空>"]
    ex, _ = sentiment.expiring_contracts(rows, "2026-09-29", None)
    assert ex == set()


def test_pc_oi_0924_no_expiry_matches_official():
    row = sentiment.compute_day("2026-09-24", _txo_fetch, HOL)
    assert (row["put_oi"], row["call_oi"], row["pc_oi"]) == (59603, 69848, 85.33)
    assert row["cv"] == 2


def test_pc_oi_0929_excludes_expiring_matches_official(caplog):
    with caplog.at_level(logging.INFO, logger=sentiment.logger.name):
        row = sentiment.compute_day("2026-09-29", _txo_fetch, HOL)
    assert (row["put_oi"], row["call_oi"], row["pc_oi"]) == (47553, 63148, 75.30)
    assert "202609F4(put 44870／call 65523)" in caplog.text


def test_pc_oi_0929_calendar_fail_open_does_not_exclude(caplog):
    with caplog.at_level(logging.INFO, logger=sentiment.logger.name):
        row = sentiment.compute_day("2026-09-29", _txo_fetch, None)
    assert (row["put_oi"], row["call_oi"], row["pc_oi"]) == (92423, 128671, 71.83)
    assert "行事曆未載入" in caplog.text and "TXO 無" in caplog.text


def test_volume_not_excluded():
    rows = [{"contract_date": "202609F4", "call_put": "put", "trading_session": "position",
             "open_interest": 100, "volume": 7},
            {"contract_date": "202609F4", "call_put": "call", "trading_session": "after_market",
             "open_interest": 0, "volume": 3},
            {"contract_date": "202610", "call_put": "call", "trading_session": "position",
             "open_interest": 50, "volume": 5}]
    pc = sentiment.pc_ratios(rows, {"202609F4"})
    assert (pc["put_oi"], pc["call_oi"]) == (0, 50)
    assert (pc["put_vol"], pc["call_vol"]) == (7, 8)
    assert pc["ex_oi"] == {"202609F4": {"put": 100, "call": 0}}


def test_mtx_excludes_expiring_both_bases():
    d = "2026-09-29"
    mtx = [{"futures_id": "MTX", "contract_date": "202609F4", "trading_session": "position", "open_interest": 400},
           {"futures_id": "MTX", "contract_date": "202609W5", "trading_session": "position", "open_interest": 600},
           {"futures_id": "MTX", "contract_date": "202610", "trading_session": "position", "open_interest": 7000}]
    ex, _ = sentiment.expiring_contracts(mtx, d, HOL)
    rt = sentiment.retail_ratio(mtx, INST_ROWS, exclude=ex)
    assert rt["mtx_oi_all"] == 7600 and rt["mtx_oi_monthly_only"] == 7000
    assert rt["mtx_ex_oi"] == {"202609F4": 400}
    assert rt["retail_ratio"] == 97.57                  # 7415 / 7600 = 97.565… → half-up 97.57
    assert (rt["inst_long"], rt["inst_short"]) == (3304, 10719)   # 法人多空不受影響


def test_update_loads_calendar_once_and_passes_it(tmp_path, monkeypatch):
    loads = []
    monkeypatch.setattr(twse_holidays, "load", lambda *a, **k: loads.append(1) or HOL)
    out = sentiment.update(["2026-09-24", "2026-09-29"], fetch=_txo_fetch, taifex_fetch=lambda: [],
                           path=tmp_path / "s.json")
    assert loads == [1]
    assert [r["pc_oi"] for r in out["rows"]] == [85.33, 75.30]


def test_update_calendar_fail_open_logs(tmp_path, caplog):
    # conftest 讓預設行事曆抓取離線失敗 → load() 回 None → 只排週末
    with caplog.at_level(logging.INFO):
        out = sentiment.update(["2026-09-29"], fetch=_txo_fetch, taifex_fetch=lambda: [],
                               path=tmp_path / "s.json")
    assert out["rows"][0]["pc_oi"] == 71.83
    assert "休市行事曆未載入" in caplog.text


def test_update_no_todo_does_not_load_calendar(tmp_path, monkeypatch):
    monkeypatch.setattr(twse_holidays, "load", lambda *a, **k: pytest.fail("不該讀行事曆"))
    sentiment.update([], fetch=_txo_fetch, taifex_fetch=lambda: [], path=tmp_path / "s.json")


def test_row_cv():
    assert sentiment.row_cv({}) == 1 and sentiment.row_cv({"cv": True}) == 1
    assert sentiment.row_cv({"cv": "2"}) == 1 and sentiment.row_cv({"cv": 2}) == 2


def test_cv_old_rows_recomputed_oldest_first_with_cap_and_budget(tmp_path):
    cal = _cal(12)
    p = tmp_path / "s.json"
    old = []
    for d in cal:
        r = dict(sentiment.compute_day(d, fixture_fetch(), None))
        r.pop("cv")                                       # 舊版列：無 cv（＝1）
        old.append(r)
    old[3]["cv"] = 2                                      # 已是新版的列不重算
    p.write_text(json.dumps({"schema": 1, "start": sentiment.SENTIMENT_START, "rows": old}), encoding="utf-8")

    calls = []
    out = sentiment.update(cal, fetch=fixture_fetch(calls), taifex_fetch=lambda: [], path=p,
                           max_days=5, holidays=None)
    days = [d for ds, d in calls if ds == "TaiwanOptionVix"]
    assert days == [cal[0], cal[1], cal[2], cal[4], cal[-1]]   # 由舊到新（跳過 cv=2 的 cal[3]）＋最新日
    assert [sentiment.row_cv(r) for r in out["rows"]] == [2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 2]

    calls.clear()                                         # 預算仍生效：第 2 天後超標即停
    out = sentiment.update(cal, fetch=fixture_fetch(calls), taifex_fetch=lambda: [], path=p,
                           max_days=5, holidays=None, budget_sec=600, clock=_fake_clock(300))
    assert [d for ds, d in calls if ds == "TaiwanOptionVix"] == [cal[5], cal[6]]

    for _ in range(5):
        out = sentiment.update(cal, fetch=fixture_fetch(), taifex_fetch=lambda: [], path=p,
                               max_days=5, holidays=None)
    assert all(sentiment.row_cv(r) == 2 for r in out["rows"]) and [r["date"] for r in out["rows"]] == cal
