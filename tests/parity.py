#!/usr/bin/env python3
# tests/parity.py
# 前端(index.html) ↔ 後端(budget.py/sectors.py) 聚合口徑一致性測試。
#
# 動機：自訂區間 / 本週 / 上週 / 上月 走「瀏覽器端逐日 fetch + 聚合」（aggregateRange +
#   jPage*），單日與 r5/10/20/65 走後端預算（budget.aggregate + page_*）。兩份是同一套
#   口徑的兩種語言實作，沒有守門機制就會靜默漂移——2026-07 實際發生過一次：
#   bias20 的 MA20 取樣一邊是「最後 20 個有效收盤」、一邊是「最近 20 個交易日」，
#   停牌/新上市個股在兩種模式下乖離率不同。
#
# 做法：對同一個視窗 n，分別跑 JS（node tests/extract_js.mjs）與 Python，逐檔逐欄比對
#   agg、四頁 pages、以及類股資金流 sectors 摘要。
#
# 用法：
#   python tests/parity.py              # 預設比對 n=1 與 n=20
#   python tests/parity.py --n 5 65
#   python tests/parity.py --max-report 40
#
# 退出碼：0=全相符；1=有不符（列出前 N 筆差異）；2=環境/前置錯誤。

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import budget      # noqa: E402
import sectors     # noqa: E402

# 浮點容差：兩邊都已 round 過，理論上該完全相等；只留 1e-9 吸收 IEEE754 表示誤差。
# 不放寬到 0.01/0.1——那會把真正的口徑漂移（如 bias20 事故）藏起來。
EPS = 1e-9


def num_close(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    return abs(a - b) <= max(EPS, abs(a) * EPS, abs(b) * EPS)


def diff(py, js, path: str, out: list[str], limit: int) -> None:
    """遞迴比對，把差異描述 append 到 out（達 limit 即停止累積）。"""
    if len(out) >= limit:
        return
    if isinstance(py, dict) and isinstance(js, dict):
        for k in sorted(set(py) | set(js)):
            if k not in py:
                out.append(f"{path}.{k}: Python 缺此鍵（JS={js[k]!r}）")
            elif k not in js:
                out.append(f"{path}.{k}: JS 缺此鍵（Python={py[k]!r}）")
            else:
                diff(py[k], js[k], f"{path}.{k}", out, limit)
        return
    if isinstance(py, list) and isinstance(js, list):
        if len(py) != len(js):
            out.append(f"{path}: 長度不同 Python={len(py)} JS={len(js)}")
            return
        for i, (a, b) in enumerate(zip(py, js)):
            diff(a, b, f"{path}[{i}]", out, limit)
        return
    if isinstance(py, (int, float)) and isinstance(js, (int, float)):
        if not num_close(py, js):
            out.append(f"{path}: Python={py} JS={js}（差 {py - js:+g}）")
        return
    if py != js:
        out.append(f"{path}: Python={py!r} JS={js!r}")


def run_js(n: int, out_path: Path) -> dict:
    r = subprocess.run(
        ["node", str(ROOT / "tests" / "extract_js.mjs"), str(out_path), str(n)],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(f"✗ node 端失敗（n={n}）：\n{r.stderr}", file=sys.stderr)
        sys.exit(2)
    if r.stderr.strip():
        print(f"  {r.stderr.strip()}")
    return json.loads(out_path.read_text(encoding="utf-8"))


def build_python(n: int, dates, docs, meta, chain_map) -> dict:
    agg = budget.aggregate(dates, docs, meta, n)
    pages = {
        "etf": budget.page_etf(agg),
        "trust": budget.page_inst(agg, "t"),
        # 前端 jPageInst 不含 futures_card（期貨卡走 fetchTxOI 另抓），比對時排除
        "foreign": budget.page_inst(agg, "f"),
        "sync": budget.page_sync(agg),
        "oppose": budget.page_oppose(agg),
    }
    return {"agg": agg, "pages": pages, "sectors": sectors.build_view(agg, chain_map)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, nargs="+", default=[1, 20],
                    help="要比對的視窗交易日數（預設 1 20）")
    ap.add_argument("--max-report", type=int, default=25, help="每個視窗最多列幾筆差異")
    args = ap.parse_args()

    dates, docs = budget.load_daily()
    if not dates:
        print("✗ 無 daily 檔可比對", file=sys.stderr)
        sys.exit(2)
    meta = json.loads((ROOT / "data" / "meta.json").read_text(encoding="utf-8"))
    chain_map = sectors.load_chain_map()
    print(f"daily {len(dates)} 個交易日（{dates[0]} ~ {dates[-1]}）、"
          f"產業鏈對照 {len(chain_map)} 檔")

    failed = False
    with tempfile.TemporaryDirectory() as td:
        for n in args.n:
            js = run_js(n, Path(td) / f"js_{n}.json")
            py = build_python(n, dates, docs, meta, chain_map)

            diffs: list[str] = []
            diff(py["agg"], js["agg"], f"n{n}.agg", diffs, args.max_report)
            diff(py["pages"], js["pages"], f"n{n}.pages", diffs, args.max_report)
            # sectors：只比類股摘要與涵蓋率（stocks 逐檔表已由 agg 比對覆蓋）
            diff(py["sectors"]["classifications"], js["sectors"]["classifications"],
                 f"n{n}.sectors", diffs, args.max_report)

            if diffs:
                failed = True
                print(f"\n✗ n={n} 有 {len(diffs)}+ 筆差異（列前 {args.max_report}）：")
                for d in diffs[:args.max_report]:
                    print(f"    {d}")
            else:
                print(f"✓ n={n} 完全相符（agg {len(py['agg'])} 檔 + 四頁 + 類股摘要）")

    if failed:
        print("\n前後端口徑已漂移：修正 budget.py/sectors.py 或 index.html 使兩邊一致。")
        sys.exit(1)
    print("\n✓ 前後端聚合口徑一致")


if __name__ == "__main__":
    main()
