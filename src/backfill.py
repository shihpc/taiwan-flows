# src/backfill.py
# 規格步驟 3：回補歷史交易日
#
# 回補 2026-05-01 起至今（庫存累計有效區）+ 往前共 65 個交易日（價量/買賣超歷史）。
# 交易日 ≤ 2026-04-30：t_inv = null；≥ 2026-05-01：自 baseline 起逐日累計。
#
# 用法：
#   python src/backfill.py                          # 最近 65 交易日，升序回補（覆寫）
#   python src/backfill.py --days 65 --end 2026-06-12
#   python src/backfill.py --start 2026-05-01 --end 2026-06-12
#   python src/backfill.py --resume                 # 跳過已存在的 daily 檔（斷點續跑）
#
# 重要：庫存鏈需「升序」處理，每日寫檔後下一日才讀得到前日庫存。
# 預設覆寫（不 --resume），確保 2026-05-01 後的庫存鏈完整重算。

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finmind import fm_get, check_quota  # noqa: E402
from pipeline import run_date, DAILY_DIR  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill")

TPE = timezone(timedelta(hours=8))
CALENDAR_PROBE = "2330"  # 用台積電當交易日曆探針（每個交易日必有資料）


def trading_days(end: str, lookback_cal_days: int = 140) -> list[str]:
    """以 2330 在 [end-lookback, end] 的成交日作為交易日曆。"""
    start = (date.fromisoformat(end) - timedelta(days=lookback_cal_days)).isoformat()
    df = fm_get("TaiwanStockPrice", data_id=CALENDAR_PROBE, start_date=start, end_date=end)
    if df is None or df.empty:
        raise RuntimeError("無法取得交易日曆（2330 價格抓取失敗）")
    days = sorted(str(d)[:10] for d in df["date"].tolist())
    return [d for d in days if d <= end]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=65, help="回補交易日數（預設 65）")
    ap.add_argument("--start", help="起始日 YYYY-MM-DD（給定則覆蓋 --days）")
    ap.add_argument("--end", help="結束日 YYYY-MM-DD（預設最近交易日）")
    ap.add_argument("--resume", action="store_true", help="跳過已存在的 daily 檔")
    args = ap.parse_args()

    end = args.end or datetime.now(TPE).date().isoformat()
    cal = trading_days(end)
    if not cal:
        logger.error("交易日曆為空")
        return
    end = cal[-1]  # 對齊到實際最近交易日

    if args.start:
        days = [d for d in cal if d >= args.start]
    else:
        days = cal[-args.days:]

    q0 = check_quota()
    logger.info(f"回補區間 {days[0]} ~ {days[-1]}（{len(days)} 交易日）| API 用量 {q0['used']}/{q0['limit']}")

    done = skipped = 0
    for i, d in enumerate(days, 1):
        out = DAILY_DIR / f"{d.replace('-', '')}.json"
        if args.resume and out.exists():
            skipped += 1
            continue
        logger.info(f"[{i}/{len(days)}] {d} …")
        try:
            if run_date(d):
                done += 1
        except Exception as e:
            logger.error(f"{d} 失敗：{e}")

    q1 = check_quota()
    logger.info(f"完成：產出 {done}、跳過 {skipped} | API 用量 {q1['used']}/{q1['limit']}")


if __name__ == "__main__":
    main()
