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
TWSE_FMTQIK = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"   # 上市大盤每日成交金額（整月）
TPEX_SUM = "https://www.tpex.org.tw/www/zh-tw/insti/summary"
TPEX_INDEX = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingIndex"  # 上櫃大盤
HEADERS = {"User-Agent": "Mozilla/5.0"}


def _roc_to_iso(s: str) -> str | None:
    """民國 115/06/12 → 2026-06-12。"""
    try:
        y, m, d = str(s).split("/")
        return f"{int(y) + 1911}-{int(m):02d}-{int(d):02d}"
    except Exception:
        return None


def fetch_fmtqik_month(yyyymm: str) -> dict[str, dict]:
    """FMTQIK：回傳該月 {date: {"turnover_k":成交金額(千元), "taiex":發行量加權股價指數}}（上市大盤）。
    FMTQIK fields = [日期, 成交股數, 成交金額(元), 成交筆數, 發行量加權股價指數, 漲跌點數]。"""
    j = _get_json(TWSE_FMTQIK, {"date": yyyymm + "01", "response": "json"})
    out: dict[str, dict] = {}
    if not j or j.get("stat") != "OK":
        return out
    for row in j.get("data", []):
        iso = _roc_to_iso(row[0])
        if not iso:
            continue
        rec = {"turnover_k": round(float(str(row[2]).replace(",", "")) / 1000)}
        try:
            rec["taiex"] = round(float(str(row[4]).replace(",", "")), 2)  # 加權指數
        except Exception:
            pass
        out[iso] = rec
    return out


def fetch_tse_turnover_month(yyyymm: str) -> dict[str, int]:
    """FMTQIK：回傳該月 {date: 成交金額(千元)}（上市大盤，相容包裝）。"""
    return {d: r["turnover_k"] for d, r in fetch_fmtqik_month(yyyymm).items() if "turnover_k" in r}


def fetch_otc_turnover_month(yyyymm: str) -> dict[str, int]:
    """tradingIndex：回傳該月 {date_iso: 上櫃成交金額(千元)}。
    回應為整月日列 [民國日期, 成交量(千股), 成交金額(千元), 成交筆數, 指數, 漲跌]，成交金額在 index 2、已是千元。
    TPEx 部分端點 SSL 異常，必要時關閉驗證重試。"""
    out: dict[str, int] = {}
    for verify in (True, False):
        try:
            r = requests.get(TPEX_INDEX, params={"date": f"{yyyymm[:4]}/{yyyymm[4:6]}/01", "response": "json"},
                             headers=HEADERS, timeout=20, verify=verify)
            if not r.text.strip().startswith("{"):
                continue
            j = r.json()
        except Exception:
            continue
        tables = j.get("tables") if isinstance(j, dict) else None
        if not tables:
            continue
        tbl = tables[0] if isinstance(tables, list) else tables
        for row in (tbl.get("data") or []):
            iso = _roc_to_iso(str(row[0]).replace("/", "/"))
            if iso:
                try:
                    out[iso] = round(float(str(row[2]).replace(",", "")))  # 成交金額，千元
                except Exception:
                    pass
        if out:
            break
    return out

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
    """回傳 {"tse":{...}|None, "otc":{...}|None}；兩者皆 None → None。含市場成交金額 turnover_k。"""
    tse, otc = fetch_tse(d), fetch_otc(d)
    if tse is None and otc is None:
        return None
    # 市場成交金額（外資佔比分母用，千元）+ 加權指數（台指期部位市值用）：
    #   上市 FMTQIK、上櫃 tradingIndex（皆月查、取當日）
    ym = d.replace("-", "")[:6]
    rec = {"tse": tse, "otc": otc}
    if tse is not None:
        try:
            mon = fetch_fmtqik_month(ym)
            if d in mon:
                tse["turnover_k"] = mon[d].get("turnover_k")
                if "taiex" in mon[d]:
                    rec["taiex"] = mon[d]["taiex"]
        except Exception as e:
            logger.warning(f"[totals] {d} 上市成交額/指數失敗：{e}")
    if otc is not None:
        try:
            mon = fetch_otc_turnover_month(ym)
            if d in mon:
                otc["turnover_k"] = mon[d]
        except Exception as e:
            logger.warning(f"[totals] {d} 上櫃成交額失敗：{e}")
    return rec


def backfill_turnover() -> int:
    """為既有 totals.json 補市場成交金額 turnover_k（上市按月 FMTQIK 快取、上櫃逐日）。"""
    doc = load_totals()
    rows = doc["rows"]
    tse_cache: dict[str, dict] = {}
    otc_cache: dict[str, dict] = {}
    n = 0
    for d in sorted(rows):
        rec = rows[d]
        tse, otc = rec.get("tse"), rec.get("otc")
        ym = d.replace("-", "")[:6]
        need_tse = (tse is not None and "turnover_k" not in tse) or ("taiex" not in rec and tse is not None)
        if need_tse:
            if ym not in tse_cache:
                logger.info(f"FMTQIK {ym} …")
                tse_cache[ym] = fetch_fmtqik_month(ym)
            md = tse_cache[ym].get(d)
            if md:
                if "turnover_k" not in tse and "turnover_k" in md:
                    tse["turnover_k"] = md["turnover_k"]; n += 1
                if "taiex" not in rec and "taiex" in md:
                    rec["taiex"] = md["taiex"]; n += 1
        if otc is not None and "turnover_k" not in otc:
            if ym not in otc_cache:
                logger.info(f"tradingIndex {ym} …")
                otc_cache[ym] = fetch_otc_turnover_month(ym); time.sleep(0.5)
            if d in otc_cache[ym]:
                otc["turnover_k"] = otc_cache[ym][d]; n += 1
    save_totals(doc)
    logger.info(f"已補 turnover_k {n} 筆，寫回 totals.json")
    return n


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


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill-turnover", action="store_true", help="為既有 totals.json 補市場成交金額")
    a = ap.parse_args()
    if a.backfill_turnover:
        backfill_turnover()
