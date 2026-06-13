# src/totals.py
# 市場三大法人買賣超（三大法人卡用）— 資料源：證交所 BFI82U 三大法人買賣金額總表
#
# 改用證交所官方數字（FinMind 的 TaiwanStockTotalInstitutionalInvestors 在
# 部分日期會修訂出與官方/自身逐檔不一致的投信數，故直接抓 TWSE）。
#
# BFI82U JSON: https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate=YYYYMMDD&type=day&response=json
#   fields = 單位名稱, 買進金額, 賣出金額, 買賣差額（單位：元）
#   外資 = 外資及陸資(不含外資自營商) + 外資自營商
#   投信 = 投信
#   自營 = 自營商(自行買賣) + 自營商(避險)
#
# 輸出 data/totals.json：{"dates":[...], "rows":{date:{f/t/d_buy_k, _sell_k, _net_k}}}（千元）

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests

logger = logging.getLogger("totals")

ROOT = Path(__file__).resolve().parent.parent
TOTALS_PATH = ROOT / "data" / "totals.json"

TWSE_BFI = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"
HEADERS = {"User-Agent": "Mozilla/5.0"}

FOREIGN_ROWS = {"外資及陸資(不含外資自營商)", "外資自營商"}
TRUST_ROWS = {"投信"}
DEALER_ROWS = {"自營商(自行買賣)", "自營商(避險)"}


def fetch_total(d: str, retries: int = 3) -> dict | None:
    """抓 TWSE BFI82U 單日三大法人買/賣/淨（千元）。非交易日/查無 → None。

    TWSE 偶爾在正常交易日回傳莫名的 stat（如「查詢日期大於可查詢最大日期」），
    屬暫時性節流，重試數次即可。
    """
    j = None
    for attempt in range(retries):
        try:
            r = requests.get(TWSE_BFI, params={"dayDate": d.replace("-", ""), "type": "day", "response": "json"},
                             headers=HEADERS, timeout=20)
            j = r.json()
        except Exception as e:
            logger.warning(f"[BFI82U] {d} 抓取失敗 (attempt {attempt + 1})：{e}")
            j = None
        if j and j.get("stat") == "OK" and j.get("data"):
            break
        time.sleep(2.0)
    if not j or j.get("stat") != "OK" or not j.get("data"):
        return None

    num = lambda s: float(str(s).replace(",", ""))  # noqa: E731
    agg = {"f": [0.0, 0.0], "t": [0.0, 0.0], "d": [0.0, 0.0]}
    matched = set()
    for row in j["data"]:
        name, buy, sell = row[0], row[1], row[2]
        for tag, names in (("f", FOREIGN_ROWS), ("t", TRUST_ROWS), ("d", DEALER_ROWS)):
            if name in names:
                agg[tag][0] += num(buy)
                agg[tag][1] += num(sell)
                matched.add(name)
    # 結構檢查：投信與外資列必須出現，否則視為異常
    if "投信" not in matched or not (FOREIGN_ROWS & matched):
        logger.warning(f"[BFI82U] {d} 欄位結構異常，matched={matched}")
        return None

    out = {}
    for tag in ("f", "t", "d"):
        b, s = agg[tag]
        out[f"{tag}_buy_k"] = round(b / 1000)
        out[f"{tag}_sell_k"] = round(s / 1000)
        out[f"{tag}_net_k"] = round((b - s) / 1000)
    return out


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
