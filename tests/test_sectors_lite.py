#!/usr/bin/env python3
"""sector_*_lite.json 瘦身版產出的守門測試——免 token、免網路。

批次三 #17：`sector_ranges.json` 2.5MB 中有 96.6% 是逐檔 `stocks` 表，前端只在
drill-down（點類股→成分股）用得到，卻每天整檔改寫（git delta 後仍約 364KB/版本）。
現在後端同時產 lite（只有類股摘要）與 full（原格式，供回退），前端讀 lite、
drill-down 改走「逐日 daily 檔即時聚合」。

守的東西：
  1. `lite_view()` 是純函式：classifications 沿用同一物件、不重算 → 與 full 逐位相同；
     stocks 消失、補上 dates/stocks_n。
  2. 現行 `data/` 產出裡，lite 與 full 的 classifications **序列化後逐位相同**
     （單日檔＋四個窗），且 lite 完全不含 stocks。
  3. lite 的窗 meta（dates/trading_days/start/end）自洽——前端 buildSectorStocks
     直接拿 dates 去抓 daily 逐日檔重建逐檔表，錯了會整段 drill-down 對不上。
  4. 瘦身確實有發生（lite 不到 full 的 15%），避免哪天 stocks 又被塞回 lite。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import sectors  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

DUMP = dict(ensure_ascii=False, separators=(",", ":"))


def _load(name: str):
    p = DATA / name
    if not p.exists():
        pytest.skip(f"缺 data/{name}（尚未跑過 python src/sectors.py）")
    return json.loads(p.read_text(encoding="utf-8"))


# ── 1. 純函式行為 ────────────────────────────────────────────────

def _fake_view():
    agg = {
        "2330": {"code": "2330", "name": "台積電", "is_etf": False, "industry": "半導體業",
                 "close": 1000.0, "chg_pct": 1.0,
                 "f_net": 100.0, "f_amt": 100000, "t_net": 10.0, "t_amt": 10000,
                 "d_net": -5.0, "d_amt": -5000},
        "0050": {"code": "0050", "name": "元大台灣50", "is_etf": True, "industry": "ETF",
                 "close": 200.0, "chg_pct": -0.5,
                 "f_net": -20.0, "f_amt": -4000, "t_net": 0.0, "t_amt": 0,
                 "d_net": 1.0, "d_amt": 200},
    }
    return sectors.build_view(agg, {"2330": ["半導體"]})


def test_lite_view_keeps_classifications_and_drops_stocks():
    full = _fake_view()
    lite = sectors.lite_view(full, ["2026-01-02", "2026-01-03"])
    assert "stocks" not in lite
    assert lite["classifications"] is full["classifications"]          # 同一物件，不可能漂移
    assert json.dumps(lite["classifications"], **DUMP) == json.dumps(full["classifications"], **DUMP)
    assert lite["dates"] == ["2026-01-02", "2026-01-03"]
    assert lite["stocks_n"] == len(full["stocks"]) == 2
    # full 本身不被動到（雙格式並存，回退路徑要完好）
    assert "stocks" in full and len(full["stocks"]) == 2


def test_lite_view_copies_dates_list():
    """dates 必須是複本：呼叫端的 win 清單之後被改動不該影響已產出的 lite。"""
    win = ["2026-01-02"]
    lite = sectors.lite_view(_fake_view(), win)
    win.append("2026-01-03")
    assert lite["dates"] == ["2026-01-02"]


# ── 2. 現行 data/ 產出：lite 的 classifications 與 full 逐位相同 ──

def test_latest_lite_matches_full():
    full, lite = _load("sector_latest.json"), _load("sector_latest_lite.json")
    assert full["date"] == lite["date"] and full["window"] == lite["window"]
    assert json.dumps(lite["classifications"], **DUMP) == json.dumps(full["classifications"], **DUMP)
    assert "stocks" not in lite
    assert lite["stocks_n"] == len(full["stocks"])
    assert lite["dates"] == [full["date"]]


def test_ranges_lite_matches_full():
    full, lite = _load("sector_ranges.json"), _load("sector_ranges_lite.json")
    assert full["date"] == lite["date"]
    assert set(lite["windows"]) == set(full["windows"]) == {"r5", "r10", "r20", "r65"}
    for key, fw in full["windows"].items():
        lw = lite["windows"][key]
        assert json.dumps(lw["classifications"], **DUMP) == json.dumps(fw["classifications"], **DUMP), key
        assert "stocks" not in lw, key
        assert lw["stocks_n"] == len(fw["stocks"]), key
        # 窗 meta 自洽：dates 就是 [start..end] 那 trading_days 天，前端據此抓 daily
        assert lw["trading_days"] == fw["trading_days"] == len(lw["dates"]), key
        assert lw["dates"][0] == fw["start"] and lw["dates"][-1] == fw["end"], key
        assert lw["dates"] == sorted(lw["dates"]), key


def test_ranges_lite_dates_are_real_trading_days():
    """lite 的 dates 必須逐日對得上 data/daily/ 的實際檔案（前端就是照這個清單抓）。"""
    lite = _load("sector_ranges_lite.json")
    have = {f.stem for f in (DATA / "daily").glob("*.json")}
    if not have:
        pytest.skip("無 data/daily 檔")
    for key, lw in lite["windows"].items():
        missing = [d for d in lw["dates"] if d.replace("-", "") not in have]
        assert not missing, f"{key} 的 dates 有 {len(missing)} 天沒有對應的 daily 檔：{missing[:3]}"


# ── 3. 瘦身效果 ─────────────────────────────────────────────────

@pytest.mark.parametrize("full_name,lite_name", [
    ("sector_latest.json", "sector_latest_lite.json"),
    ("sector_ranges.json", "sector_ranges_lite.json"),
])
def test_lite_is_much_smaller(full_name, lite_name):
    fp, lp = DATA / full_name, DATA / lite_name
    if not (fp.exists() and lp.exists()):
        pytest.skip("缺產出檔")
    ratio = lp.stat().st_size / fp.stat().st_size
    assert ratio < 0.15, f"{lite_name} 佔 full 的 {ratio:.1%}，瘦身沒生效（stocks 是不是又被塞回去了？）"
