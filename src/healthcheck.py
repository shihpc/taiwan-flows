# src/healthcheck.py
# daily 收盤價健檢：把 data/daily 存的 close 與 FinMind 權威 TaiwanStockPrice 逐檔比對。
#
# 動機（2026-06-26 事故）：排程偶爾早於 FinMind 價格 settle，整批高價權值股被寫成
#   暫定/舊值（法人張數卻正確）→ 金額/成交值/市值/Excel/sectors 全錯。此工具自動偵測。
#
# 用法：
#   python src/healthcheck.py                 # 檢查最新交易日
#   python src/healthcheck.py --date 2026-06-26
#   python src/healthcheck.py --date 2026-06-26 --fix   # critical 時重抓該日 pipeline 後再驗
#
# 嚴重度：ok（全相符）/ warn（零星，可能是個股事件）/ critical（疑似未 settle 事故）。
# 2026-09-06 加列數健全性：當日列數低於近 20 個 daily 檔中位數的 80% → warn、50% → critical
#   （row_count_severity 純函式，價格與列數兩者取較嚴重者當 severity）。
# 設計：純函式 check() 回結果 dict（run_daily 取用寫進 status.json）；CLI 才印出/重抓。

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finmind import fm_get  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("healthcheck")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DAILY_DIR = DATA / "daily"
META_PATH = DATA / "meta.json"
TPE = timezone(timedelta(hours=8))

TOL = 0.02            # 收盤價相對誤差容忍（2%）
CRIT_PCT = 1.0       # 不符比例 ≥1% → critical
CRIT_N = 20          # 或不符檔數 ≥20 → critical
ROWS_HISTORY_N = 20   # 列數健全性：對照近 N 個 daily 檔的中位數
ROWS_WARN_RATIO = 0.8  # 低於中位數 80% → warn
ROWS_CRIT_RATIO = 0.5  # 低於中位數 50% → critical
_SEV_RANK = {"ok": 0, "warn": 1, "critical": 2}


def latest_daily_date() -> str | None:
    files = sorted(DAILY_DIR.glob("*.json"))
    if not files:
        return None
    s = files[-1].stem
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def _daily_close(date: str) -> dict[str, float]:
    p = DAILY_DIR / f"{date.replace('-', '')}.json"
    if not p.exists():
        return {}
    doc = json.loads(p.read_text(encoding="utf-8"))
    ci, cc = doc["cols"].index("close"), doc["cols"].index("code")
    return {r[cc]: r[ci] for r in doc["rows"] if r[ci]}


def _daily_rows(date: str) -> int:
    p = DAILY_DIR / f"{date.replace('-', '')}.json"
    if not p.exists():
        return 0
    return len(json.loads(p.read_text(encoding="utf-8")).get("rows", []))


def _row_counts_before(date: str, n: int = ROWS_HISTORY_N) -> list[int]:
    """date 之前（不含）最近 n 個 daily 檔的列數，舊→新。"""
    stem = date.replace("-", "")
    files = sorted(f for f in DAILY_DIR.glob("*.json") if f.stem < stem)[-n:]
    out = []
    for f in files:
        try:
            out.append(len(json.loads(f.read_text(encoding="utf-8")).get("rows", [])))
        except Exception:
            continue
    return out


def row_count_severity(n_rows: int, history: list[int]) -> tuple[str, float | None]:
    """列數健全性（純函式）：回 (severity, 歷史中位數)。

    n_rows 低於中位數 ROWS_WARN_RATIO（80%）→ warn、低於 ROWS_CRIT_RATIO（50%）→ critical。
    沒有歷史可對照時回 ok（無從判斷、不亂報）。動機：FinMind 偶爾只回半套股價/法人
    （例如上櫃缺席），價格逐檔比對全相符、但整張表少一半——列數是價格健檢看不到的維度。
    """
    hist = [h for h in history if h]
    if not hist:
        return "ok", None
    med = float(sorted(hist)[len(hist) // 2]) if len(hist) % 2 else \
        (sorted(hist)[len(hist) // 2 - 1] + sorted(hist)[len(hist) // 2]) / 2
    if n_rows < med * ROWS_CRIT_RATIO:
        return "critical", med
    if n_rows < med * ROWS_WARN_RATIO:
        return "warn", med
    return "ok", med


def worst_severity(*sevs: str) -> str:
    return max(sevs, key=lambda x: _SEV_RANK.get(x, 0))


def _auth_close(date: str) -> dict[str, float] | None:
    df = fm_get("TaiwanStockPrice", start_date=date, end_date=date)
    if df is None:
        return None  # API 失敗
    if df.empty:
        return {}
    df = df.copy()
    df["stock_id"] = df["stock_id"].astype(str)
    out = {}
    for x in df.itertuples():
        c = pd.to_numeric(x.close, errors="coerce")
        if pd.notna(c):
            out[str(x.stock_id)] = float(c)
    return out


def check(date: str, tol: float = TOL) -> dict:
    """逐檔比對 daily.close vs 權威 close。回 severity/統計/最差名單。"""
    daily = _daily_close(date)
    auth = _auth_close(date)
    res = {"date": date, "checked_at": datetime.now(TPE).isoformat()}
    if not daily:
        return {**res, "severity": "no_data", "note": "無 daily 檔"}
    if auth is None:
        return {**res, "severity": "unknown", "note": "權威源查詢失敗"}
    if not auth:
        return {**res, "severity": "unknown", "note": "權威源當日無資料（非交易日？）"}

    n = bad = 0
    worst = []
    for code, dc in daily.items():
        ac = auth.get(code)
        if not (ac and dc):
            continue
        n += 1
        ratio = ac / dc
        if abs(ratio - 1) > tol:
            bad += 1
            worst.append({"code": code, "daily": round(dc, 2), "auth": round(ac, 2),
                          "ratio": round(ratio, 2)})
    pct = round(bad / n * 100, 2) if n else 0.0
    worst.sort(key=lambda w: abs(w["ratio"] - 1), reverse=True)
    price_sev = "ok" if bad == 0 else ("critical" if (pct >= CRIT_PCT or bad >= CRIT_N) else "warn")
    # 列數健全性（與價格比對獨立的維度，取較嚴重者）
    n_rows = _daily_rows(date)
    rows_sev, rows_med = row_count_severity(n_rows, _row_counts_before(date))
    return {**res, "severity": worst_severity(price_sev, rows_sev),
            "price_severity": price_sev, "compared": n, "mismatch": bad,
            "mismatch_pct": pct, "worst": worst[:5],
            "rows": n_rows, "rows_median": rows_med, "rows_severity": rows_sev}


def remediate(date: str) -> dict:
    """critical 時重抓該日 pipeline（FinMind 已 settle 才有效）後再驗。"""
    from pipeline import run_date
    logger.warning(f"{date} 價格疑似異常，重抓 pipeline …")
    run_date(date)
    return check(date)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="交易日（預設最新 daily）")
    ap.add_argument("--fix", action="store_true", help="critical 時重抓 pipeline 後再驗")
    args = ap.parse_args()
    d = args.date or latest_daily_date()
    if not d:
        logger.error("無 daily 檔可檢查")
        sys.exit(2)

    r = check(d)
    icon = {"ok": "✓", "warn": "⚠", "critical": "✗"}.get(r["severity"], "?")
    logger.info(f"[{icon} {r['severity']}] {d}：比對 {r.get('compared','-')} 檔，"
                f"不符 {r.get('mismatch','-')}（{r.get('mismatch_pct','-')}%）")
    for w in r.get("worst", []):
        logger.info(f"    {w['code']}: daily {w['daily']} vs 權威 {w['auth']}  x{w['ratio']}")

    if r["severity"] == "critical" and args.fix:
        r = remediate(d)
        icon = {"ok": "✓", "warn": "⚠", "critical": "✗"}.get(r["severity"], "?")
        logger.info(f"[重抓後 {icon} {r['severity']}] 不符 {r.get('mismatch','-')}（{r.get('mismatch_pct','-')}%）")

    sys.exit(0 if r["severity"] in ("ok", "warn") else 1)


if __name__ == "__main__":
    main()
