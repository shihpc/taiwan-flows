# src/rebuild_daily.py
# 重建逐檔 daily/*.json（只重抓 price+inst+share，不動 futures/totals）。
# 用於 daily schema 變更後（如新增 f/t/d 的 buy/sell 欄）重跑歷史。
# 依 meta.calendar 升序處理（投信庫存鏈才正確）。

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finmind import check_quota  # noqa: E402
from pipeline import (COLS, DAILY_DIR, META_PATH, build_rows, fetch_day,  # noqa: E402
                      load_meta, running_inventory, update_issued_lots)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("rebuild_daily")


def main() -> None:
    meta = load_meta()
    cal = meta["calendar"]
    q0 = check_quota()
    logger.info(f"重建 {cal[0]} ~ {cal[-1]}（{len(cal)} 交易日）| API {q0['used']}/{q0['limit']}")
    done = 0
    for i, d in enumerate(cal, 1):
        try:
            dfs = fetch_day(d)
            if dfs is None:
                logger.warning(f"{d} 無價格資料，跳過")
                continue
            prev_inv = running_inventory(d)
            rows = build_rows(d, dfs, prev_inv, meta)
            (DAILY_DIR / f"{d.replace('-', '')}.json").write_text(
                json.dumps({"date": d, "cols": COLS, "rows": rows}, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8")
            update_issued_lots(meta, dfs[2])
            done += 1
            if i % 10 == 0:
                logger.info(f"  [{i}/{len(cal)}] {d}")
        except Exception as e:
            logger.error(f"{d} 失敗：{e}")
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    q1 = check_quota()
    logger.info(f"完成：重建 {done} 日 | API {q1['used']}/{q1['limit']}")


if __name__ == "__main__":
    main()
