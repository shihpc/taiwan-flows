# src/backfill_market.py
# 回補市場層資料（不含逐檔 daily）：
#   - data/futures/*.json（期貨三大法人，item 2 台指期比較基準用）
#   - data/totals.json（市場總三大法人，item 4 三大法人卡用）
#
# 依 meta.calendar（已回補的交易日）逐日補齊。期貨檔已存在則略過。
#
# 用法：python src/backfill_market.py

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finmind import check_quota  # noqa: E402
from futures import fetch_futures, FUT_DIR  # noqa: E402
from totals import load_totals, save_totals, update_total  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_market")

ROOT = Path(__file__).resolve().parent.parent
META_PATH = ROOT / "data" / "meta.json"


def main() -> None:
    cal = json.loads(META_PATH.read_text(encoding="utf-8"))["calendar"]
    q0 = check_quota()
    logger.info(f"回補市場資料 {cal[0]} ~ {cal[-1]}（{len(cal)} 交易日）| API {q0['used']}/{q0['limit']}")

    totals = load_totals()
    fut_done = tot_done = 0
    for i, d in enumerate(cal, 1):
        fut_file = FUT_DIR / f"{d.replace('-', '')}.json"
        if not fut_file.exists():
            if fetch_futures(d) is not None:
                fut_done += 1
        if d not in totals["rows"]:
            if update_total(d, doc=totals, save=False) is not None:
                tot_done += 1
        if i % 10 == 0:
            logger.info(f"  [{i}/{len(cal)}] {d}")
    save_totals(totals)

    q1 = check_quota()
    logger.info(f"完成：期貨 +{fut_done}、totals +{tot_done}（共 {len(totals['rows'])} 日）| API {q1['used']}/{q1['limit']}")


if __name__ == "__main__":
    main()
