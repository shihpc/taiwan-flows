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


def build_tie_agg(dates, docs, meta) -> tuple[dict, dict]:
    """人造「處處同分」的 agg，專打排序 tie-break。

    為什麼需要：真實資料在多數排行的主鍵上沒有 tie，把 budget.py 的次鍵 `code`
    整個拿掉，parity 的五個視窗照樣全綠——8 個排序站點只有「佔成交量」那個
    因為常出現整數比而測得出來。同分時的排名取決於語言的物件走訪順序
    （JS 把像整數的鍵排在字串鍵前並依數值遞增，Python dict 依 meta.stocks 插入序），
    所以必須自己造出同分才驗得到。

    做法：拿真實 agg 當模板（確保欄位齊全、型別正確），只覆蓋參與排序的欄位為定值。
    代號刻意混三種形狀：像整數的（2330）、有前導 0 的（0050）、帶字母的（00637L）。
    """
    real = budget.aggregate(dates, docs, meta, 1)
    picked, seen = {}, {"int": 0, "zero": 0, "alpha": 0}
    for code, a in real.items():
        if code.isdigit() and not code.startswith("0"):
            kind = "int"
        elif code.isdigit():
            kind = "zero"
        else:
            kind = "alpha"
        if seen[kind] >= 8:
            continue
        seen[kind] += 1
        picked[code] = dict(a)
        if sum(seen.values()) >= 24:
            break

    tie_agg, sectors_map = {}, {}
    for i, (code, a) in enumerate(picked.items()):
        # 一半當買方一半當賣方；ETF 與非 ETF 各半，讓 page_etf 的兩個 top 也吃到同分
        sign = 1 if i % 2 == 0 else -1
        a.update({
            "close": 100.0, "vol": 1000, "amt": 500000, "issued_lots": 2000.0,
            "chg_pct": 1.0, "bias20": 2.0,
            "f_net": 100.0 * sign, "f_amt": 50000 * sign,
            "t_net": 100.0 * sign, "t_amt": 50000 * sign,
            "d_net": 100.0 * sign, "d_amt": 50000 * sign,
            "f_buy": 10.0, "f_sell": 10.0, "t_buy": 10.0, "t_sell": 10.0,
            "d_buy": 10.0, "d_sell": 10.0,
            "f_buy_amt": 1000, "f_sell_amt": 1000, "t_buy_amt": 1000,
            "t_sell_amt": 1000, "d_buy_amt": 1000, "d_sell_amt": 1000,
            "t_inv": 500.0, "f_shares": 500.0, "f_pct": 25.0,
            "is_etf": i % 3 == 0,          # 三分之一當 ETF
            # 每檔各自一個類股，且金額同分 → 類股摘要的排序完全由 tie-break 決定。
            # 用共用類股名不行：兩邊「第一次遇到某類股」的順序若相同，沒帶次鍵也會通過。
            "industry": f"測試類股{code}",
        })
        # 對作頁要兩個方向都有資料才驗得到：i%4==0 造 外資買·投信賣、
        # i%4==3 造 外資賣·投信買（只翻其中一邊的話另一個清單會是空的，測不到）
        if i % 4 in (0, 3):
            a["t_net"], a["t_amt"] = -a["t_net"], -a["t_amt"]
        tie_agg[code] = a
        sectors_map[code] = {"i": [f"測試鏈{code}"], "s": [], "p": []}
    return tie_agg, sectors_map


def check_ties(td: Path, max_report: int) -> list[str]:
    """對人造同分資料比對前後端的排序結果。"""
    dates, docs = budget.load_daily()
    meta = json.loads((ROOT / "data" / "meta.json").read_text(encoding="utf-8"))
    tie_agg, chain_raw = build_tie_agg(dates, docs, meta)

    agg_path, chain_path, out_path = td / "tie_agg.json", td / "tie_chain.json", td / "tie_js.json"
    agg_path.write_text(json.dumps(tie_agg), encoding="utf-8")
    chain_path.write_text(json.dumps(chain_raw), encoding="utf-8")

    r = subprocess.run(
        ["node", str(ROOT / "tests" / "extract_js.mjs"), str(out_path), "1",
         "--agg", str(agg_path), "--chain", str(chain_path)],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(f"✗ node 端失敗（ties）：\n{r.stderr}", file=sys.stderr)
        sys.exit(2)
    if r.stderr.strip():
        print(f"  {r.stderr.strip()}")
    js = json.loads(out_path.read_text(encoding="utf-8"))

    py_pages = {
        "etf": budget.page_etf(tie_agg),
        "trust": budget.page_inst(tie_agg, "t"),
        "foreign": budget.page_inst(tie_agg, "f"),
        "sync": budget.page_sync(tie_agg),
        "oppose": budget.page_oppose(tie_agg),
    }
    chain_map = {c: v["i"] for c, v in chain_raw.items()}
    py_sectors = sectors.build_view(tie_agg, chain_map)

    diffs: list[str] = []
    diff(py_pages, js["pages"], "ties.pages", diffs, max_report)
    diff(py_sectors["classifications"], js["sectors"]["classifications"],
         "ties.sectors", diffs, max_report)
    return diffs


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
    ap.add_argument("--no-ties", action="store_true", help="略過排序 tie-break 測試")
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

        # 排序 tie-break：真實資料多數排行沒有同分，必須另外造
        if not args.no_ties:
            tie_diffs = check_ties(Path(td), args.max_report)
            if tie_diffs:
                failed = True
                print(f"\n✗ 排序 tie-break 有 {len(tie_diffs)}+ 筆差異（列前 {args.max_report}）：")
                for d in tie_diffs[:args.max_report]:
                    print(f"    {d}")
            else:
                print("✓ 排序 tie-break 完全相符（人造同分資料，四頁 + 類股摘要）")

    if failed:
        print("\n前後端口徑已漂移：修正 budget.py/sectors.py 或 index.html 使兩邊一致。")
        sys.exit(1)
    print("\n✓ 前後端聚合口徑一致")


if __name__ == "__main__":
    main()
