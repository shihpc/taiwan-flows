# src/totals.py
# 市場三大法人買賣超（三大法人卡用）— 上市(TWSE) + 上櫃(TPEx)
#
# 上市：證交所 BFI82U 三大法人買賣金額總表
#   https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate=YYYYMMDD&type=day&response=json
# 上櫃：櫃買中心 三大法人買賣金額彙總表
#   https://www.tpex.org.tw/www/zh-tw/insti/summary?type=Daily&date=YYYY/MM/DD&response=json
#
# 外資 = 外資及陸資(含自營商)；投信 = 投信；自營 = 自營商(自行+避險)。
# 輸出 data/totals.json：{"dates":[...], "rows":{date:{"tse":{...}|null,"otc":{...}|null}}}
#   每個市場含 f/t/d 的 _buy_k/_sell_k/_net_k（千元）。合計由前端 tse+otc 相加。

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
TPEX_SUM = "https://www.tpex.org.tw/www/zh-tw/insti/summary"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# 上市 BFI82U 列名（無「合計」列，需加總分項）
TSE_FOREIGN = {"外資及陸資(不含外資自營商)", "外資自營商"}
TSE_TRUST = {"投信"}
TSE_DEALER = {"自營商(自行買賣)", "自營商(避險)"}
# 上櫃 TPEx 有「合計」列，直接取
OTC_FOREIGN = {"外資及陸資合計"}
OTC_TRUST = {"投信"}
OTC_DEALER = {"自營商合計"}


def _norm(name: str) -> str:
    return str(name).replace("　", "").strip()


def _parse_rows(rows, foreign, trust, dealer) -> dict | None:
    num = lambda s: float(str(s).replace(",", ""))  # noqa: E731
    agg = {"f": [0.0, 0.0], "t": [0.0, 0.0], "d": [0.0, 0.0]}
    matched = set()
    for row in rows:
        name = _norm(row[0])
        for tag, names in (("f", foreign), ("t", trust), ("d", dealer)):
            if name in names:
                agg[tag][0] += num(row[1])
                agg[tag][1] += num(row[2])
                matched.add(name)
    if "投信" not in matched or not (foreign & matched):
        return None
    out = {}
    for tag in ("f", "t", "d"):
        b, s = agg[tag]
        out[f"{tag}_buy_k"] = round(b / 1000)
        out[f"{tag}_sell_k"] = round(s / 1000)
        out[f"{tag}_net_k"] = round((b - s) / 1000)
    return out


def _get_json(url, params, retries=3):
    """重試包裝（TWSE/TPEx 偶發節流或亂 stat）。"""
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=20)
            j = r.json()
            return j
        except Exception as e:
            logger.warning(f"[totals] {url} 失敗 (attempt {attempt + 1})：{e}")
            time.sleep(2.0)
    return None


def fetch_tse(d: str) -> dict | None:
    j = _get_json(TWSE_BFI, {"dayDate": d.replace("-", ""), "type": "day", "response": "json"})
    if not j or j.get("stat") != "OK" or not j.get("data"):
        # 可能是節流亂 stat，再試一次（慢）
        time.sleep(2.0)
        j = _get_json(TWSE_BFI, {"dayDate": d.replace("-", ""), "type": "day", "response": "json"}, retries=2)
    if not j or j.get("stat") != "OK" or not j.get("data"):
        return None
    return _parse_rows(j["data"], TSE_FOREIGN, TSE_TRUST, TSE_DEALER)


def fetch_otc(d: str) -> dict | None:
    j = _get_json(TPEX_SUM, {"type": "Daily", "date": d.replace("-", "/"), "response": "json"})
    if not j or str(j.get("stat")).lower() != "ok" or not j.get("tables"):
        return None
    tables = j["tables"]
    tbl = tables[0] if isinstance(tables, list) else tables
    data = tbl.get("data") or []
    if not data:
        return None
    return _parse_rows(data, OTC_FOREIGN, OTC_TRUST, OTC_DEALER)


def fetch_total(d: str) -> dict | None:
    """回傳 {"tse":{...}|None, "otc":{...}|None}；兩者皆 None → None。"""
    tse, otc = fetch_tse(d), fetch_otc(d)
    if tse is None and otc is None:
        return None
    return {"tse": tse, "otc": otc}


def load_totals() -> dict:
    if TOTALS_PATH.exists():
        return json.loads(TOTALS_PATH.read_text(encoding="utf-8"))
    return {"dates": [], "rows": {}}


def save_totals(doc: dict) -> None:
    doc["dates"] = sorted(doc["rows"])
    TOTALS_PATH.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def update_total(d: str, doc: dict | None = None, save: bool = True) -> dict | None:
    rec = fetch_total(d)
    if rec is None:
        return None
    if doc is None:
        doc = load_totals()
    doc["rows"][d] = rec
    if save:
        save_totals(doc)
    return rec
