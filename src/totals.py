# src/totals.py
# 市場總三大法人買賣超（item 4 三大法人卡用）
#
# FinMind TaiwanStockTotalInstitutionalInvestors（全市場合計，金額單位「元」）
#   names: Foreign_Investor, Foreign_Dealer_Self, Investment_Trust,
#          Dealer_self, Dealer_Hedging, total
#   外資 = Foreign_Investor + Foreign_Dealer_Self
#   投信 = Investment_Trust
#   自營 = Dealer_self + Dealer_Hedging
#   net = buy − sell，本檔統一存「千元」
#
# 輸出 data/totals.json：{"dates":[...], "rows":{date:{f_net_k,t_net_k,d_net_k}}}

from __future__ import annotations

import json
import logging
from pathlib import Path

from finmind import fm_get

logger = logging.getLogger("totals")

ROOT = Path(__file__).resolve().parent.parent
TOTALS_PATH = ROOT / "data" / "totals.json"

FOREIGN = {"Foreign_Investor", "Foreign_Dealer_Self"}
TRUST = {"Investment_Trust"}
DEALER = {"Dealer_self", "Dealer_Hedging"}


def fetch_total(d: str) -> dict | None:
    """單日市場三大法人淨買賣超（千元）。"""
    df = fm_get("TaiwanStockTotalInstitutionalInvestors", start_date=d, end_date=d)
    if df is None or df.empty:
        return None
    df = df.copy()
    df["net"] = (df["buy"].astype(float) - df["sell"].astype(float)) / 1000.0  # 千元
    pick = lambda names: round(float(df[df["name"].isin(names)]["net"].sum()))  # noqa: E731
    return {"f_net_k": pick(FOREIGN), "t_net_k": pick(TRUST), "d_net_k": pick(DEALER)}


def load_totals() -> dict:
    if TOTALS_PATH.exists():
        return json.loads(TOTALS_PATH.read_text(encoding="utf-8"))
    return {"dates": [], "rows": {}}


def save_totals(doc: dict) -> None:
    doc["dates"] = sorted(doc["rows"])
    TOTALS_PATH.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def update_total(d: str, doc: dict | None = None, save: bool = True) -> dict | None:
    """抓 d 當日並寫入 totals.json。doc 可傳入以批次累積（背景回補用）。"""
    rec = fetch_total(d)
    if rec is None:
        return None
    if doc is None:
        doc = load_totals()
    doc["rows"][d] = rec
    if save:
        save_totals(doc)
    return rec
