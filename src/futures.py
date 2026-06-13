# src/futures.py
# 期貨三大法人未平倉（規格 4.4 外資頁頁首卡用）
#
# FinMind TaiwanFuturesInstitutionalInvestors
#   futures_id='TX'（台指期）, institutional_investors∈{外資,投信,自營商}
#   *_open_interest_balance_volume/amount = 未平倉口數/金額
#
# 外資台指期未平倉淨額：
#   口數淨額 = long_oi_volume − short_oi_volume
#   金額淨額(千) = (long_oi_amount − short_oi_amount) / 1000

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from finmind import fm_get

logger = logging.getLogger("futures")

ROOT = Path(__file__).resolve().parent.parent
FUT_DIR = ROOT / "data" / "futures"

FOREIGN = "外資"
TX = "TX"


def fetch_futures(d: str, save: bool = True) -> pd.DataFrame | None:
    """抓單日期貨三大法人，存 data/futures/YYYYMMDD.json（緊湊）。"""
    df = fm_get("TaiwanFuturesInstitutionalInvestors", start_date=d, end_date=d)
    if df is None or df.empty:
        return None
    if save:
        FUT_DIR.mkdir(parents=True, exist_ok=True)
        out = {"date": d, "cols": list(df.columns), "rows": df.values.tolist()}
        (FUT_DIR / f"{d.replace('-', '')}.json").write_text(
            json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return df


def _load_local(d: str) -> pd.DataFrame | None:
    f = FUT_DIR / f"{d.replace('-', '')}.json"
    if not f.exists():
        return None
    doc = json.loads(f.read_text(encoding="utf-8"))
    return pd.DataFrame(doc["rows"], columns=doc["cols"])


def tx_foreign_oi(d: str) -> dict | None:
    """
    取 d 當日外資台指期未平倉淨額。優先讀本地檔，無則抓 API。
    回傳 {date, oi_net_lots, oi_net_amount_k} 或 None。
    """
    df = _load_local(d)
    if df is None:
        df = fetch_futures(d)
    if df is None or df.empty:
        return None
    sub = df[(df["futures_id"] == TX) & (df["institutional_investors"] == FOREIGN)]
    if sub.empty:
        return None
    r = sub.iloc[0]
    lots = int(r["long_open_interest_balance_volume"]) - int(r["short_open_interest_balance_volume"])
    amt_k = round((float(r["long_open_interest_balance_amount"])
                   - float(r["short_open_interest_balance_amount"])) / 1000)
    return {"date": d, "oi_net_lots": lots, "oi_net_amount_k": amt_k}
