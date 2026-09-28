# src/run_daily.py
# 每日排程入口（GitHub Actions 呼叫）：
#   判斷交易日 → pipeline(daily + futures) → budget(latest + ranges) → status.json
#
# 用法：
#   python src/run_daily.py                # 今天（台北時區）
#   python src/run_daily.py --date 2026-06-12
#
# pipeline 回 False（無資料）時不再一律標 `no_data`（2026-09-06 修），改由純函式
#   classify_no_data() 三分：
#     no_data  週末或國定假日（非交易日）    → exit 0，daily.yml 不重試
#              （國定假日 2026-09-28 起讀家族行事曆 twse_holidays.py；讀不到 fail-open 只排週末）
#     waiting  平日、台北 20:00 發布截止前     → exit 0，daily.yml 不重試（哨兵/下一班會再來）
#     missing  平日、已過截止仍無資料（預期交易日缺料）→ exit 1，daily.yml 重試、用盡後亮紅告警
#   舊行為把三者全寫成 no_data 且 workflow 視同成功，真交易日缺料會被靜默吞掉。
#   status.json 另新增 expected_date / actual_date / last_attempt_at / last_success_at
#   （既有 date / status / note / sources / healthcheck 語意不變，前端照舊讀）。
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
import twse_holidays  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_daily")

TPE = timezone(timedelta(hours=8))
DATA = Path(__file__).resolve().parent.parent / "data"
STATUS_PATH = DATA / "status.json"

# 盤後資料發布截止（台北時）：三大法人買賣超官方約 15:00 後陸續發布、外資持股
# （TaiwanStockShareholding）官方 21:00 更新（見 CLAUDE.md「快速接手」），哨兵時窗
# 17:00–22:55。取 20:00 當「平日到了這個時間還沒有股價/法人資料就該視為缺料」的分界：
# 早於它＝資料還沒出（waiting），晚於它＝預期交易日缺料（missing）。
PUBLISH_DEADLINE_HOUR = 20


def target_trading_day() -> str:
    """本次執行對應的目標交易日（台北 YYYY-MM-DD）。

    daily.yml cron 排台北 21:19，主觸發是 live-v2 Worker 哨兵（台北 17:00–22:55）；
    但 GitHub Actions 常延遲 1~3 小時，一旦延到隔日凌晨才啟動，直接用
    `datetime.now(TPE)` 會把目標日滾成「隔天」——去抓一個還沒開盤的新交易日，
    FinMind 當然無資料 → 誤標 `no_data`。

    2026-08-01 01:10 的實例：status.json 寫 `{"date":"2026-08-01","status":"no_data"}`，
    但同檔 `sources` 四項都是 `2026-07-31`（那是從既有資料檔讀出來的）——資料明明
    是好的，狀態卻報無資料，前端右上角因此顯示「尚未開盤/非交易日」。

    故凌晨啟動（hour < 12）時把目標交易日回推一天＝觸發當晚的交易日；21:19 準點或
    小延遲（hour >= 12）則就是當日。與 postmkt `build_summary.py` 的 `slot_trading_day()`
    同一套處理（該處 2026-07-17 已修，本處是同類 bug 的漏網）。

    **刻意不跳過週末／國定假日**（2026-09-28 評估）：這裡回的是「觸發當晚的日曆日」，不是
    「最近一個交易日」。回推到假日（例：09-29 01:10 啟動 → 09-28 教師節）時，run_date 無資料，
    交給 classify_no_data 依行事曆判 no_data（exit 0）即可——週末本來就是同一條路。若在這裡
    往前跳到上一個交易日，會把「假日當晚的這一班」改成重跑上一交易日：冪等無害但會重寫
    status.date／expected_date 成舊日期，前端與 verify 就看不到「今天休市」這件事，得不償失。
    """
    now = datetime.now(TPE)
    if now.hour < 12:
        return (now - timedelta(days=1)).date().isoformat()
    return now.date().isoformat()


def classify_no_data(target_day: str, now_tpe: datetime,
                     calendar: list[str] | None = None,
                     holidays: "twse_holidays.TwseHolidays | None" = None) -> str:
    """pipeline 對 target_day 回「無資料」時的三分類（純函式，免 token 免網路）。

    回傳值 → status.json.status：
      "no_data"  週末或國定假日：非交易日，正常沒資料。
      "waiting"  平日、now_tpe 尚未到 target_day 的發布截止（台北 PUBLISH_DEADLINE_HOUR:00）：
                 資料還沒出，等哨兵/備援 cron 下一班再來，不算異常。
      "missing"  平日、已過截止仍無資料：預期交易日缺料，要重試、要告警。

    calendar＝meta.calendar（歷來已產出的交易日）：target_day 已在其中代表它「確定是交易日」
    （曾成功抓過），跳過週末推定直接走截止判定；它只含過去、不含未來，所以幫不了假日判斷。

    國定假日（2026-09-28 起）：holidays＝家族共用行事曆（twse_holidays.load() 的結果，規格見
    taiwan-flow-live-v2 docs/holiday-calendar.md）。target_day 在其 closed 內且該年度有涵蓋
    → no_data，與週末同類（exit 0、不重試、不告警）。**fail-open**：holidays 為 None（行事曆
    404／逾時／壞檔）或目標年度不在 years 裡 → 退回只排週末，此時平日國定假日過 20:00 仍會
    被判 missing（誤報一次，同改動前）——寧可假日誤報，也不可讓行事曆掛掉時把真交易日吞掉。
    calendar 規則優先於行事曆：已成功抓過的日子一定是交易日，行事曆若誤列也不採信。
    颱風臨時停市：TWSE 事後才補進行事曆，當天仍可能誤報一次（規格 §2 已知、接受）。

    now_tpe 可能已跨到隔日凌晨（Actions 延遲，target_day 是前一天）——截止是以 target_day
    當天 20:00 定義的，隔日凌晨自然算「已過截止」。
    """
    t = datetime.strptime(target_day, "%Y-%m-%d")
    known_trading_day = bool(calendar) and target_day in calendar
    if not known_trading_day and (t.weekday() >= 5  # 5=週六 6=週日
                                  or twse_holidays.is_holiday(target_day, holidays)):
        return "no_data"
    deadline = t.replace(hour=PUBLISH_DEADLINE_HOUR, minute=0, second=0, microsecond=0,
                         tzinfo=TPE)
    if now_tpe.astimezone(TPE) < deadline:
        return "waiting"
    return "missing"


def load_calendar() -> list[str]:
    try:
        return json.loads((DATA / "meta.json").read_text(encoding="utf-8")).get("calendar", [])
    except Exception:
        return []


def latest_daily_date() -> str | None:
    """data/daily/ 最新檔的日期（＝實際最新落地的交易日），給 status.json.actual_date。"""
    files = sorted((DATA / "daily").glob("*.json"))
    if not files:
        return None
    s = files[-1].stem
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


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


def write_status(date: str, status: str, note: str = "", healthcheck: dict | None = None, *,
                 expected_date: str | None = None, success: bool = False,
                 attempt: bool = True) -> None:
    """寫 status.json。

    既有欄位語意不變：date（本次目標交易日）/ status（ok/no_data/waiting/missing/error）/
    note / sources / checked_at / healthcheck。2026-09-06 新增：
      expected_date   本次預期的交易日（預設＝date；verify_daily 依它驗，不回退到昨天的檔）
      actual_date     data/daily/ 最新檔日期＝實際落地的最新交易日；與 expected_date 不同即缺料
      last_attempt_at 最近一次 pipeline 嘗試時間（attempt=False 時沿用舊值，verify 只更新健檢用）
      last_success_at 最近一次成功產出時間：success=True 才更新，失敗時保留舊值
    """
    prev = read_status()
    now = datetime.now(TPE).isoformat()
    payload = {"date": date, "status": status, "note": note,
               "expected_date": expected_date or date,
               "actual_date": latest_daily_date(),
               "sources": gather_sources(),
               "checked_at": now,
               "last_attempt_at": now if attempt else prev.get("last_attempt_at"),
               "last_success_at": now if success else prev.get("last_success_at")}
    if healthcheck is not None:
        payload["healthcheck"] = healthcheck  # daily 收盤價 vs 權威源（severity: ok/warn/critical）
    STATUS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")


def rebuild_products() -> None:
    """由現成 daily 重算所有衍生產出（不打 FinMind 逐檔 API）。

    run_daily 與 verify_daily（延後驗證重抓後）共用：只要 data/daily 變了，
    latest / latest_ranges / foreign_history / sector_*（含 _lite 瘦身版）都要跟著重算。
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
        # sectors.main 一次產四份：full（sector_latest/ranges.json，供 SECTOR_SOURCE="full" 回退）
        # ＋ lite（sector_*_lite.json，前端預設讀，不含逐檔 stocks）
        logger.info("重算 sector_latest/ranges.json ＋ sector_*_lite.json …")
        sectors.main([])
    except Exception as e:
        logger.warning(f"sectors 失敗（略過）：{e}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="交易日 YYYY-MM-DD（預設今天）")
    args = ap.parse_args(argv)
    d = args.date or target_trading_day()

    now = datetime.now(TPE)
    if not args.date and d != now.date().isoformat():
        logger.info(f"凌晨 {now:%H:%M} 啟動（Actions 延遲跨午夜），目標交易日回推為 {d}")
    logger.info(f"=== 每日排程 {d} ===")
    try:
        produced = run_date(d)
    except Exception as e:
        logger.error(f"pipeline 失敗：{e}")
        write_status(d, "error", str(e))
        sys.exit(1)

    if not produced:
        hol = twse_holidays.load()   # fail-open：讀不到回 None＝只排週末，不拋例外
        kind = classify_no_data(d, datetime.now(TPE), load_calendar(), hol)
        if kind == "no_data":
            if twse_holidays.is_holiday(d, hol) and datetime.strptime(d, "%Y-%m-%d").weekday() < 5:
                nm = hol.name(d)
                reason = f"國定假日{'：' + nm if nm else ''}"
            else:
                reason = "週末"
            logger.warning(f"{d} 為{reason}（非交易日），無產出")
            write_status(d, "no_data", f"非交易日（{reason}）")
            return  # exit 0，daily.yml 不重試
        if kind == "waiting":
            logger.warning(f"{d} 平日、尚未到台北 {PUBLISH_DEADLINE_HOUR:02d}:00 發布截止，資料尚未發布")
            write_status(d, "waiting", f"等待資料發布（台北 {PUBLISH_DEADLINE_HOUR:02d}:00 截止前）")
            return  # exit 0，daily.yml 不重試（哨兵/備援 cron 下一班會再來）
        logger.error(f"{d} 預期交易日已過台北 {PUBLISH_DEADLINE_HOUR:02d}:00 仍無資料 → missing")
        note = "預期交易日缺料（已過發布截止仍無資料）"
        if hol is None or not hol.covers(d):
            note = "預期交易日缺料（已過發布截止仍無資料；國定假日行事曆未涵蓋此日，若為假日屬誤報）"
        write_status(d, "missing", note)
        sys.exit(1)  # daily.yml 依 status 重試、用盡後亮紅告警

    rebuild_products()

    # 收盤價健檢留給 verify.yml（23:40 台北）的延後獨立驗證；這裡先標 pending，
    # 讓前端知道「今天的資料還沒經過獨立驗證」而不是誤以為已驗過。
    write_status(d, "ok", "", healthcheck={
        "date": d, "severity": "pending",
        "note": "待 verify_daily 延後驗證（同一次排程內比對權威源無意義）",
    }, success=True)
    logger.info(f"=== {d} 完成 ===")


if __name__ == "__main__":
    main()
