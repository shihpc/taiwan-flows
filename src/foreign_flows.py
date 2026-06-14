# src/foreign_flows.py
# 「外資買賣超」tab 的市場別月/年歷史（上市 TSE + 上櫃 OTC）。
#
# 資料源（官方為準）：
#   TSE（上市）外資 = 證交所 BFI82U「外資及陸資(不含外資自營商)」+「外資自營商」買/賣金額。
#     批量取得：FinMind TaiwanStockTotalInstitutionalInvestors（源自證交所，外資淨額與官方 ~97% 吻合，
#     近 65 日再用 data/totals.json 官方值覆蓋，確保最新與頭條精準）。
#   OTC（上櫃）外資 = 櫃買 insti/summary 的「外資及陸資合計」。
#     近期：data/totals.json（官方 TPEx）。歷史：foreign_backfill.py 逐日 TPEx 回補 → data/_otc_daily.json。
#
# 金額單位：全程千元（_k），與 totals.json 一致。前端 ÷1e5 → 億。
#
# 輸出 data/foreign_history.json：
#   {generated_at, latest_date,
#    monthly:{ "YYYY-MM": {tse:{buy_k,sell_k,net_k}, otc:{...}|null} },
#    daily:  { "YYYY-MM-DD": {tse:{...}, otc:{...}|null} }   # 近 ~30 交易日，供本週/上週/近5日 }
# 年總、本週/上週/近5日由前端依 monthly/daily 聚合。

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finmind import fm_get  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("foreign_flows")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TOTALS_PATH = DATA / "totals.json"
OTC_DAILY_PATH = DATA / "_otc_daily.json"      # 逐日 OTC 外資原始快取（backfill 產出）
OUT_PATH = DATA / "foreign_history.json"

TPE = timezone(timedelta(hours=8))
START_YEAR = 2024
FOREIGN_NAMES = {"Foreign_Investor", "Foreign_Dealer_Self"}
RECENT_DAILY_KEEP = 30   # daily 區只留最近 N 交易日（本週/上週/近5日 夠用）


# ════════════════════════════════════════════════════════════════
# TSE：FinMind 批量外資日資料（2024 → 今）
# ════════════════════════════════════════════════════════════════

def fetch_tse_daily() -> dict[str, dict]:
    """回傳 {date: {buy_k, sell_k, net_k}}（上市外資，千元）。FinMind 按年分段抓避免巨量回應。"""
    today = datetime.now(TPE).date()
    out: dict[str, dict] = {}
    for yr in range(START_YEAR, today.year + 1):
        s = f"{yr}-01-01"
        e = f"{yr}-12-31" if yr < today.year else today.isoformat()
        logger.info(f"FinMind 上市外資 {s} ~ {e} …")
        df = fm_get("TaiwanStockTotalInstitutionalInvestors", start_date=s, end_date=e)
        if df is None or df.empty:
            logger.warning(f"  {yr} 無資料")
            continue
        sub = df[df["name"].isin(FOREIGN_NAMES)]
        for d, g in sub.groupby("date"):
            b = float(g["buy"].sum()) / 1000.0
            s_ = float(g["sell"].sum()) / 1000.0
            out[d] = {"buy_k": round(b), "sell_k": round(s_), "net_k": round(b - s_)}
    logger.info(f"上市外資日資料 {len(out)} 天")
    return out


def overlay_official_tse(tse: dict[str, dict], totals: dict) -> int:
    """近期用 totals.json 官方 TSE 外資覆蓋 FinMind（精準對齊證交所）。"""
    n = 0
    for d, rec in totals.get("rows", {}).items():
        t = rec.get("tse")
        if t:
            tse[d] = {"buy_k": t["f_buy_k"], "sell_k": t["f_sell_k"], "net_k": t["f_net_k"]}
            n += 1
    return n


# ════════════════════════════════════════════════════════════════
# OTC：totals.json（近期官方）+ _otc_daily.json（歷史 backfill）
# ════════════════════════════════════════════════════════════════

def load_otc_daily(totals: dict) -> dict[str, dict]:
    """回傳 {date: {buy_k, sell_k, net_k}}（上櫃外資，千元）。"""
    out: dict[str, dict] = {}
    if OTC_DAILY_PATH.exists():
        raw = json.loads(OTC_DAILY_PATH.read_text(encoding="utf-8"))
        for d, v in raw.get("rows", {}).items():
            if v:
                out[d] = v
    # totals.json 官方覆蓋近期
    for d, rec in totals.get("rows", {}).items():
        o = rec.get("otc")
        if o:
            out[d] = {"buy_k": o["f_buy_k"], "sell_k": o["f_sell_k"], "net_k": o["f_net_k"]}
    return out


# ════════════════════════════════════════════════════════════════
# 聚合
# ════════════════════════════════════════════════════════════════

def _add(acc: dict, rec: dict) -> None:
    acc["buy_k"] += rec["buy_k"]
    acc["sell_k"] += rec["sell_k"]
    acc["net_k"] += rec["net_k"]


def build(tse: dict[str, dict], otc: dict[str, dict]) -> dict:
    all_dates = sorted(set(tse) | set(otc))
    monthly: dict[str, dict] = {}
    for d in all_dates:
        ym = d[:7]
        m = monthly.setdefault(ym, {"tse": {"buy_k": 0, "sell_k": 0, "net_k": 0},
                                    "otc": {"buy_k": 0, "sell_k": 0, "net_k": 0},
                                    "_otc_days": 0, "_tse_days": 0})
        if d in tse:
            _add(m["tse"], tse[d]); m["_tse_days"] += 1
        if d in otc:
            _add(m["otc"], otc[d]); m["_otc_days"] += 1
    # OTC 該月若無任何官方日資料 → null（避免半個月假象）
    for ym, m in monthly.items():
        if m["_otc_days"] == 0:
            m["otc"] = None
        m.pop("_otc_days"); m.pop("_tse_days")

    recent = all_dates[-RECENT_DAILY_KEEP:]
    daily = {d: {"tse": tse.get(d), "otc": otc.get(d)} for d in recent}

    return {
        "generated_at": datetime.now(TPE).isoformat(),
        "latest_date": all_dates[-1] if all_dates else None,
        "monthly": monthly,
        "daily": daily,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-finmind", action="store_true", help="略過 FinMind（僅用既有快取，測試用）")
    args = ap.parse_args()

    totals = json.loads(TOTALS_PATH.read_text(encoding="utf-8")) if TOTALS_PATH.exists() else {"rows": {}}

    tse = {} if args.no_finmind else fetch_tse_daily()
    n_ov = overlay_official_tse(tse, totals)
    logger.info(f"官方 TSE 覆蓋 {n_ov} 天")

    otc = load_otc_daily(totals)
    logger.info(f"上櫃外資日資料 {len(otc)} 天（含 totals 官方近期）")

    doc = build(tse, otc)
    OUT_PATH.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    nm = len(doc["monthly"])
    nm_otc = sum(1 for m in doc["monthly"].values() if m["otc"] is not None)
    logger.info(f"已寫入 foreign_history.json：{nm} 個月（OTC 有值 {nm_otc} 月），latest {doc['latest_date']}")


if __name__ == "__main__":
    main()
