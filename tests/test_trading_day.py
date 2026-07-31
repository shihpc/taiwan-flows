#!/usr/bin/env python3
"""run_daily.target_trading_day() 的邊界回歸測試（免 token、免網路）。

守的具體 bug（2026-08-01）：原本 `d = args.date or datetime.now(TPE).date().isoformat()`，
GitHub Actions 延遲跨午夜啟動時目標日會滾成隔天，去抓一個還沒開盤的交易日 → FinMind
無資料 → 誤標 `no_data`。現場證據：status.json 寫 `{"date":"2026-08-01","status":"no_data"}`
但同檔 sources 四項都是 `2026-07-31`。

同類 bug 在 postmkt `build_summary.py` 已於 2026-07-17 修（`slot_trading_day()`），
本 repo 是漏網的那個——所以這裡補測試，別再讓它回來。

用法：python tests/test_trading_day.py
"""
from __future__ import annotations

import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import run_daily  # noqa: E402

TPE = timezone(timedelta(hours=8))

# (情境, 執行當下的台北時間, 期望的目標交易日)
CASES = [
    # hour >= 12：就是當日
    ("21:19 cron 準點",            datetime(2026, 7, 31, 21, 19, tzinfo=TPE), "2026-07-31"),
    ("17:00 哨兵窗開始",           datetime(2026, 7, 31, 17, 0,  tzinfo=TPE), "2026-07-31"),
    ("22:55 哨兵窗尾聲",           datetime(2026, 7, 31, 22, 55, tzinfo=TPE), "2026-07-31"),
    ("23:59 當日極限",             datetime(2026, 7, 31, 23, 59, tzinfo=TPE), "2026-07-31"),
    ("12:00 中午分界（含）",       datetime(2026, 8, 1, 12, 0,   tzinfo=TPE), "2026-08-01"),
    # hour < 12：回推一天
    ("00:00 剛跨午夜",             datetime(2026, 8, 1, 0, 0,    tzinfo=TPE), "2026-07-31"),
    ("01:10 實際事故現場",         datetime(2026, 8, 1, 1, 10,   tzinfo=TPE), "2026-07-31"),
    ("11:59 上午極限",             datetime(2026, 8, 1, 11, 59,  tzinfo=TPE), "2026-07-31"),
    # 跨月、跨年
    ("跨月 09-01 02:00",           datetime(2026, 9, 1, 2, 0,    tzinfo=TPE), "2026-08-31"),
    ("跨年 01-01 03:00",           datetime(2027, 1, 1, 3, 0,    tzinfo=TPE), "2026-12-31"),
]


def main() -> int:
    ok = fail = 0
    for name, now, expect in CASES:
        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        with mock.patch.object(run_daily, "datetime", FrozenDatetime):
            got = run_daily.target_trading_day()

        if got == expect:
            ok += 1
        else:
            fail += 1
            print(f"  ✗ {name}：得到 {got}，期望 {expect}")

    print(f"target_trading_day: {ok} 通過 / {fail} 失敗")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
