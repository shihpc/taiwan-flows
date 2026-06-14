# src/foreign_backfill.py
# 逐日回補上櫃(OTC)外資買賣金額 → data/_otc_daily.json（resumable）。
# 櫃買 insti/summary?type=Daily 每日一呼叫，外資列＝「外資及陸資合計」。
# 交易日曆來自 FinMind（2330 價格範圍）。已抓過的日期會跳過，可中斷重跑。
#
# 用法：python src/foreign_backfill.py [--start 2024-01-01] [--end 2026-03-10]

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import urllib3

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finmind import fm_get  # noqa: E402

urllib3.disable_warnings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("foreign_backfill")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OTC_DAILY_PATH = DATA / "_otc_daily.json"
TPE = timezone(timedelta(hours=8))
TPEX_SUM = "https://www.tpex.org.tw/www/zh-tw/insti/summary"
HEADERS = {"User-Agent": "Mozilla/5.0"}
OTC_FOREIGN = {"外資及陸資合計"}


def _norm(s: str) -> str:
    return str(s).replace("　", "").strip()


def trading_days(start: str, end: str) -> list[str]:
    df = fm_get("TaiwanStockPrice", data_id="2330", start_date=start, end_date=end)
    if df is None or df.empty:
        return []
    return sorted(df["date"].astype(str).unique())


def fetch_otc_foreign(d: str) -> dict | None:
    """單日上櫃外資 {buy_k, sell_k, net_k}（千元）。"""
    params = {"type": "Daily", "date": d.replace("-", "/"), "response": "json"}
    for attempt in range(5):
        try:
            r = requests.get(TPEX_SUM, params=params, headers=HEADERS, timeout=20, verify=False)
            if not r.text.strip().startswith("{"):
                time.sleep(3); continue
            j = r.json()
            if str(j.get("stat")).lower() != "ok" or not j.get("tables"):
                time.sleep(3); continue
            tables = j["tables"]
            tbl = tables[0] if isinstance(tables, list) else tables
            for row in (tbl.get("data") or []):
                if _norm(row[0]) in OTC_FOREIGN:
                    b = float(str(row[1]).replace(",", "")) / 1000.0
                    s = float(str(row[2]).replace(",", "")) / 1000.0
                    return {"buy_k": round(b), "sell_k": round(s), "net_k": round(b - s)}
            return None  # 當日有回應但無外資列（可能非交易日）
        except Exception as e:
            logger.warning(f"  {d} attempt {attempt+1}: {str(e)[:60]}")
            time.sleep(4)
    return None


def load_store() -> dict:
    if OTC_DAILY_PATH.exists():
        return json.loads(OTC_DAILY_PATH.read_text(encoding="utf-8"))
    return {"rows": {}}


def save_store(store: dict) -> None:
    OTC_DAILY_PATH.write_text(json.dumps(store, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=(datetime.now(TPE).date()).isoformat())
    args = ap.parse_args()

    logger.info(f"取交易日曆 {args.start} ~ {args.end} …")
    days = trading_days(args.start, args.end)
    logger.info(f"共 {len(days)} 交易日")

    store = load_store()
    done = set(store["rows"])
    todo = [d for d in days if d not in done]
    logger.info(f"已有 {len(done)} 天，待補 {len(todo)} 天")

    for i, d in enumerate(todo, 1):
        rec = fetch_otc_foreign(d)
        store["rows"][d] = rec  # 可能為 None（記錄已嘗試，避免重抓）
        if i % 10 == 0 or i == len(todo):
            save_store(store)
            logger.info(f"  [{i}/{len(todo)}] {d} 已存（最新 net億 {None if not rec else round(rec['net_k']/100000,1)}）")
        time.sleep(1.0)
    save_store(store)
    n_ok = sum(1 for v in store["rows"].values() if v)
    logger.info(f"完成。_otc_daily.json 共 {len(store['rows'])} 天（有值 {n_ok}）")


if __name__ == "__main__":
    main()
