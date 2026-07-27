# src/verify_daily.py
# 延後獨立驗證班（verify.yml，23:40 台北）：
#   healthcheck.check(最新交易日) → critical 時重抓 pipeline → 再驗 → 有改善就重算衍生產出
#   → 結果寫回 status.json.healthcheck
#
# 為什麼要「延後」跑（2026-06-26 事故的正解）：
#   healthcheck 的權威源就是 pipeline 用的 TaiwanStockPrice。同一次排程內前後相隔
#   幾秒去比對，拿到的必然是同一份（可能同樣未 settle 的）回應 → severity 恆為 ok，
#   等於沒檢查。要抓到「排程早於 FinMind settle」這類事故，必須隔數小時再獨立比對。
#   6/26 之所以驗得出來，正是因為當時是事後用 --date 手動跑的。
#
# 重抓策略：延後數小時後 FinMind 通常已 settle，此時重抓才有意義（21:19 當下重抓
#   多半仍拿到同一批未 settle 的值），所以這裡預設就會 --fix，不像原本只印建議。
#
# 用法：
#   python src/verify_daily.py                    # 驗最新 daily 那天（會重抓修復）
#   python src/verify_daily.py --date 2026-06-26
#   python src/verify_daily.py --no-fix           # 只驗不修（觀察用）
#
# 退出碼：0=ok/warn/已修復；1=仍 critical（讓 workflow 亮紅通知）；2=環境錯誤。

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import healthcheck  # noqa: E402
import run_daily  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("verify_daily")

ICON = {"ok": "✓", "warn": "⚠", "critical": "✗"}

# 退出碼語意（verify.yml 依此決定要不要亮紅）：
#   0 = 驗過且沒事（ok/warn）
#   1 = 價格事故（critical，重抓後仍未修復）→ 要通知
#   2 = 這次驗不了（權威源查詢失敗 unknown／無 daily 檔／健檢本身丟例外）
#       ——不是資料錯，別報成 critical
EXIT_OK, EXIT_CRITICAL, EXIT_UNUSABLE = 0, 1, 2


def _write_status(d: str, r: dict) -> None:
    """只更新 status.json 的 healthcheck 欄，其餘（date/status/note）沿用當日排程寫的值。"""
    st = run_daily.read_status()
    run_daily.write_status(st.get("date", d), st.get("status", "ok"),
                           st.get("note", ""), healthcheck=r)


def _log_result(prefix: str, r: dict) -> None:
    sev = r.get("severity", "?")
    logger.info(f"[{ICON.get(sev, '?')} {sev}] {prefix}比對 {r.get('compared', '-')} 檔，"
                f"不符 {r.get('mismatch', '-')}（{r.get('mismatch_pct', '-')}%）")
    for w in r.get("worst", []):
        logger.info(f"    {w['code']}: daily {w['daily']} vs 權威 {w['auth']}  x{w['ratio']}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="交易日（預設最新 daily）")
    ap.add_argument("--no-fix", action="store_true",
                    help="critical 時只回報、不重抓（預設會重抓並重算衍生產出）")
    args = ap.parse_args(argv)

    d = args.date or healthcheck.latest_daily_date()
    if not d:
        logger.error("無 daily 檔可驗證")
        sys.exit(EXIT_UNUSABLE)

    logger.info(f"=== 延後驗證 {d} ===")
    try:
        r = healthcheck.check(d)
    except Exception as e:   # 缺 token、網路層例外等：屬「無法驗證」，不是價格事故
        logger.error(f"健檢無法執行：{e}")
        _write_status(d, {"date": d, "severity": "unknown", "note": f"健檢無法執行：{e}"})
        sys.exit(EXIT_UNUSABLE)
    _log_result("", r)

    if r["severity"] == "critical" and not args.no_fix:
        logger.error(f"⚠ {d} 收盤價疑似未 settle（{r.get('mismatch')} 檔不符）→ 重抓 pipeline")
        r = healthcheck.remediate(d)      # 重抓該日 pipeline 後再驗
        _log_result("重抓後 ", r)
        # 不論有沒有修好都要重算。remediate() 內部已經呼叫 run_date() **覆寫了
        # data/daily/<d>.json**，若這裡因為「仍 critical」就跳過重算，latest/
        # latest_ranges/sector_* 仍是舊 daily 算出來的 → verify.yml 的 Commit & push
        # 是無條件執行的，會把「新 daily + 舊衍生產出」這種互相矛盾的狀態推上 main，
        # 比單純沒修好更糟。維持「衍生產出永遠由當前 daily 算出」這個不變式。
        logger.info("重抓後重算 latest/ranges/foreign/sectors（維持與 daily 一致）")
        run_daily.rebuild_products()
        if r["severity"] not in ("ok", "warn"):
            logger.error("重抓後仍 critical：FinMind 可能尚未 settle，明日排程或手動再驗")

    _write_status(d, r)
    logger.info(f"=== 驗證完成，status.json.healthcheck.severity={r['severity']} ===")
    # 退出碼三分：讓 workflow 能區分「價格事故」與「這次驗不了」，
    # 否則 FinMind 掛掉也會被報成 critical，久了真的 critical 就沒人看了。
    if r["severity"] in ("ok", "warn"):
        sys.exit(EXIT_OK)
    sys.exit(EXIT_CRITICAL if r["severity"] == "critical" else EXIT_UNUSABLE)


if __name__ == "__main__":
    main()
