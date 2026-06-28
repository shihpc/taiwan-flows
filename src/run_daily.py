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
import foreign_flows  # noqa: E402
import sectors  # noqa: E402
import healthcheck  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_daily")

TPE = timezone(timedelta(hours=8))
DATA = Path(__file__).resolve().parent.parent / "data"
STATUS_PATH = DATA / "status.json"


def gather_sources() -> dict:
    """各資料源「最新有資料日」，給前端各卡/tab 標示資料源日期、偵測落後。"""
    src: dict[str, str] = {}
    try:  # 逐檔法人/股價（FinMind）→ meta.calendar 末日
        cal = json.loads((DATA / "meta.json").read_text(encoding="utf-8")).get("calendar", [])
        if cal:
            src["daily"] = cal[-1]
    except Exception:
        pass
    try:  # 市場三大法人（證交所 BFI82U + 櫃買 TPEx）
        tot = json.loads((DATA / "totals.json").read_text(encoding="utf-8"))
        ds = tot.get("dates") or sorted(tot.get("rows", {}))
        if ds:
            src["totals"] = ds[-1]
    except Exception:
        pass
    try:  # 台指期未平倉（期交所）→ futures 最新檔
        files = sorted((DATA / "futures").glob("*.json"))
        if files:
            s = files[-1].stem
            src["futures"] = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    except Exception:
        pass
    try:  # 外資買賣超官方歷史
        fh = json.loads((DATA / "foreign_history.json").read_text(encoding="utf-8"))
        if fh.get("latest_date"):
            src["foreign"] = fh["latest_date"]
    except Exception:
        pass
    return src


def write_status(date: str, status: str, note: str = "", healthcheck: dict | None = None) -> None:
    payload = {"date": date, "status": status, "note": note,
               "sources": gather_sources(),
               "checked_at": datetime.now(TPE).isoformat()}
    if healthcheck is not None:
        payload["healthcheck"] = healthcheck  # daily 收盤價 vs 權威源（severity: ok/warn/critical）
    STATUS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
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

    # 收盤價健檢：daily.close vs 權威源（偵測「抓到未 settle 價格」事故）— 非致命，僅標警示
    hc = None
    try:
        hc = healthcheck.check(d)
        sev = hc.get("severity")
        if sev == "critical":
            logger.error(f"⚠ 價格健檢 CRITICAL：{hc.get('mismatch')} 檔不符（{hc.get('mismatch_pct')}%）"
                         f"，疑似 FinMind 未 settle；建議稍後 `healthcheck.py --date {d} --fix` 重抓")
        elif sev == "warn":
            logger.warning(f"價格健檢 warn：{hc.get('mismatch')} 檔零星不符")
        else:
            logger.info(f"價格健檢 {sev}")
    except Exception as e:
        logger.warning(f"價格健檢失敗（略過）：{e}")

    logger.info("重算 latest.json + latest_ranges.json …")
    argv = sys.argv
    sys.argv = [argv[0]]  # 隔離 argv，避免 budget argparse 吃到 --date
    try:
        budget.main()  # 預設含期貨卡
    finally:
        sys.argv = argv

    # 外資買賣超歷史（market 別月/年）— 非致命
    try:
        logger.info("重算 foreign_history.json …")
        argv = sys.argv
        sys.argv = [argv[0]]
        try:
            foreign_flows.main()
        finally:
            sys.argv = argv
    except Exception as e:
        logger.warning(f"foreign_flows 失敗（略過）：{e}")

    # 類股資金流（交易所產業別 / 產業鏈）— 非致命；讀現成 daily，不再打 FinMind
    try:
        logger.info("重算 sector_latest.json + sector_ranges.json …")
        argv = sys.argv
        sys.argv = [argv[0]]
        try:
            sectors.main()
        finally:
            sys.argv = argv
    except Exception as e:
        logger.warning(f"sectors 失敗（略過）：{e}")

    write_status(d, "ok", "", healthcheck=hc)
    logger.info(f"=== {d} 完成 ===")


if __name__ == "__main__":
    main()
