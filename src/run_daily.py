# src/run_daily.py
# 每日排程入口（GitHub Actions 呼叫）：
#   判斷交易日 → pipeline(daily + futures) → budget(latest + ranges) → status.json
#
# 用法：
#   python src/run_daily.py                # 今天（台北時區）
#   python src/run_daily.py --date 2026-06-12
#
# 非交易日 / FinMind 尚未更新：pipeline 回 False → 寫 status.json 標記，
#   exit code 0（讓 workflow 正常結束、不寄失敗信；前端依 status 顯示「資料未更新」）。

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import run_date  # noqa: E402
import budget  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_daily")

TPE = timezone(timedelta(hours=8))
STATUS_PATH = Path(__file__).resolve().parent.parent / "data" / "status.json"


def write_status(date: str, status: str, note: str = "") -> None:
    STATUS_PATH.write_text(
        json.dumps({"date": date, "status": status, "note": note,
                    "checked_at": datetime.now(TPE).isoformat()},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="交易日 YYYY-MM-DD（預設今天）")
    args = ap.parse_args()
    d = args.date or datetime.now(TPE).date().isoformat()

    logger.info(f"=== 每日排程 {d} ===")
    try:
        produced = run_date(d)
    except Exception as e:
        logger.error(f"pipeline 失敗：{e}")
        write_status(d, "error", str(e))
        sys.exit(1)

    if not produced:
        logger.warning(f"{d} 非交易日或 FinMind 尚未更新，無產出")
        write_status(d, "no_data", "非交易日或資料尚未更新")
        return  # exit 0

    logger.info("重算 latest.json + latest_ranges.json …")
    argv = sys.argv
    sys.argv = [argv[0]]  # 隔離 argv，避免 budget argparse 吃到 --date
    try:
        budget.main()  # 預設含期貨卡
    finally:
        sys.argv = argv
    write_status(d, "ok", "")
    logger.info(f"=== {d} 完成 ===")


if __name__ == "__main__":
    main()
