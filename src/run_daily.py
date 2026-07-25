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
#
# 收盤價健檢**不在這裡**跑：healthcheck 比對的權威源就是 pipeline 剛用過的
#   TaiwanStockPrice，同一次排程內前後相隔幾秒、拿到的必然是同一份（可能同樣未
#   settle 的）回應，severity 幾乎恆為 ok，抓不到 2026-06-26 那類事故。
#   改由 verify.yml（23:40 台北）跑 src/verify_daily.py 做延後獨立驗證。

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


def read_status() -> dict:
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_status(date: str, status: str, note: str = "", healthcheck: dict | None = None) -> None:
    payload = {"date": date, "status": status, "note": note,
               "sources": gather_sources(),
               "checked_at": datetime.now(TPE).isoformat()}
    if healthcheck is not None:
        payload["healthcheck"] = healthcheck  # daily 收盤價 vs 權威源（severity: ok/warn/critical）
    STATUS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")


def rebuild_products() -> None:
    """由現成 daily 重算所有衍生產出（不打 FinMind 逐檔 API）。

    run_daily 與 verify_daily（延後驗證重抓後）共用：只要 data/daily 變了，
    latest / latest_ranges / foreign_history / sector_* 都要跟著重算。
    各子模組的 main 皆接受 argv，明確傳 [] 表示「不吃上層 CLI 參數」。
    """
    logger.info("重算 latest.json + latest_ranges.json …")
    budget.main([])  # 預設含期貨卡

    # 外資買賣超歷史（market 別月/年）— 非致命
    try:
        logger.info("重算 foreign_history.json …")
        foreign_flows.main([])
    except Exception as e:
        logger.warning(f"foreign_flows 失敗（略過）：{e}")

    # 類股資金流（交易所產業別 / 產業鏈）— 非致命；讀現成 daily，不再打 FinMind
    try:
        logger.info("重算 sector_latest.json + sector_ranges.json …")
        sectors.main([])
    except Exception as e:
        logger.warning(f"sectors 失敗（略過）：{e}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="交易日 YYYY-MM-DD（預設今天）")
    args = ap.parse_args(argv)
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

    rebuild_products()

    # 收盤價健檢留給 verify.yml（23:40 台北）的延後獨立驗證；這裡先標 pending，
    # 讓前端知道「今天的資料還沒經過獨立驗證」而不是誤以為已驗過。
    write_status(d, "ok", "", healthcheck={
        "date": d, "severity": "pending",
        "note": "待 verify_daily 延後驗證（同一次排程內比對權威源無意義）",
    })
    logger.info(f"=== {d} 完成 ===")


if __name__ == "__main__":
    main()
