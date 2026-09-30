#!/usr/bin/env python3
"""一次性診斷：TXO Put/Call 未平倉比口徑（2026-09-30）。

背景：src/sentiment.py 的 pc_oi 在 2026-09-29 算出 71.83%，期交所 OpenAPI 官方
`PutCallOIRatio%`＝75.30%；成交量比兩邊都是 90.39%（一致）。docs/sentiment-tab.md §0 記載
2026-09-24 曾逐位吻合（85.33%）。本工具在有 FINMIND_TOKEN 的 GitHub runner 上
（.github/workflows/diag-sentiment.yml，只有 workflow_dispatch）把 TaiwanOptionDaily TXO
當日資料拆開印出，並列出多種候選口徑，標出哪個與官方逐位相同。

**只印、不寫任何檔**；所有例外與 log 經 finmind.mask_secret，不印 token／FinMind 請求 URL。
分組與口徑計算是純函式（tests/test_diag_pc_oi.py 離線驗證）。

用法：python tools/diag_pc_oi.py [--dates 2026-09-24,2026-09-29]
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from budget import jround  # noqa: E402
from finmind import mask_secret  # noqa: E402
import sentiment  # noqa: E402
import twse_holidays  # noqa: E402

DEFAULT_DATES = "2026-09-24,2026-09-29"
YMD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SESSIONS = ("position", "after_market")


# ════════════════════════════════════════════════════════════════
# 純函式
# ════════════════════════════════════════════════════════════════

def contract_kind(cd) -> str:
    """contract_date 形狀分類：monthly＝恰 6 位數字（YYYYMM）；weekly＝含 W（如 202610W1）；
    other＝其餘（空值、含 / 的組合、其他長度）。"""
    s = str(cd if cd is not None else "").strip()
    if re.fullmatch(r"\d{6}", s):
        return "monthly"
    if "W" in s.upper():
        return "weekly"
    return "other"


def _sess(r) -> str:
    return str(r.get("trading_session") or "").strip()


def group_sums(rows: Iterable[dict], keyfn: Callable[[dict], tuple]) -> dict[tuple, dict]:
    """依 keyfn(row) 分組加總 OI／volume 並計列數。回傳 {key: {oi, vol, n}}（key 已排序）。"""
    out: dict[tuple, dict] = defaultdict(lambda: {"oi": 0.0, "vol": 0.0, "n": 0})
    for r in rows or []:
        k = keyfn(r)
        g = out[k]
        g["oi"] += sentiment._num(r.get("open_interest"))
        g["vol"] += sentiment._num(r.get("volume"))
        g["n"] += 1
    return {k: out[k] for k in sorted(out, key=lambda t: tuple(str(x) for x in t))}


def pc_from(rows: Iterable[dict], pred: Callable[[dict], bool], field: str = "open_interest") -> dict:
    """在 pred 為真的列上算 Σput÷Σcall（百分比兩位，budget.jround）。"""
    put = call = 0.0
    n = 0
    for r in rows or []:
        cp = sentiment._cp(r.get("call_put"))
        if cp is None or not pred(r):
            continue
        n += 1
        v = sentiment._num(r.get(field))
        if cp == "put":
            put += v
        else:
            call += v
    pc = jround(put / call * 100, 2) if call else None
    return {"put": int(put), "call": int(call), "pc": pc, "n": n}


def dedup_max(rows: Iterable[dict]) -> list[dict]:
    """同一 (contract_date, strike_price, call_put, trading_session) 若有多列只留 OI 最大者
    （檢查 FinMind 是否有重複列灌水）。"""
    best: dict[tuple, dict] = {}
    for r in rows or []:
        k = (str(r.get("contract_date")), str(r.get("strike_price")),
             sentiment._cp(r.get("call_put")), _sess(r))
        if k not in best or sentiment._num(r.get("open_interest")) > sentiment._num(best[k].get("open_interest")):
            best[k] = r
    return list(best.values())


def candidates(rows: list[dict], d: str | None = None, holidays=None) -> dict[str, dict]:
    """候選口徑 → {put, call, pc, n}。"current(position)" 等於 sentiment.pc_ratios 不帶 exclude（cv1 舊口徑）。

    給 d 時另加 "position,exclude_expiring"：排除到期日＝d 的契約（sentiment.contract_expiry，
    holidays＝twse_holidays.TwseHolidays 或 None＝只排週末），即 cv2 現行口徑（§0b）。
    """
    rows = list(rows or [])
    pos = lambda r: _sess(r) == "position"  # noqa: E731
    both = lambda r: _sess(r) in SESSIONS  # noqa: E731
    monthly = sorted({str(r.get("contract_date")).strip() for r in rows
                      if contract_kind(r.get("contract_date")) == "monthly"})
    near = monthly[0] if monthly else None
    out = {
        "current(position)": pc_from(rows, pos),
        "position+after_market": pc_from(rows, both),
        "any_session": pc_from(rows, lambda r: True),
        "position,exclude_weekly": pc_from(rows, lambda r: pos(r) and contract_kind(r.get("contract_date")) != "weekly"),
        "position,weekly_only": pc_from(rows, lambda r: pos(r) and contract_kind(r.get("contract_date")) == "weekly"),
        "position,monthly_only(6碼)": pc_from(rows, lambda r: pos(r) and contract_kind(r.get("contract_date")) == "monthly"),
        "position,exclude_other": pc_from(rows, lambda r: pos(r) and contract_kind(r.get("contract_date")) != "other"),
        "position,dedup_max": pc_from(dedup_max(rows), pos),
        "after_market_only": pc_from(rows, lambda r: _sess(r) == "after_market"),
    }
    if d:
        ex, _ = sentiment.expiring_contracts(rows, d, holidays)
        out["position,exclude_expiring"] = pc_from(
            rows, lambda r: pos(r) and sentiment._cd(r) not in ex)
    if near:
        out[f"position,near_month_only({near})"] = pc_from(
            rows, lambda r: pos(r) and str(r.get("contract_date")).strip() == near)
        out[f"position,exclude_near_month({near})"] = pc_from(
            rows, lambda r: pos(r) and str(r.get("contract_date")).strip() != near)
    for cd in sorted({str(r.get("contract_date")).strip() for r in rows}):
        out[f"position,contract={cd}"] = pc_from(rows, lambda r, cd=cd: pos(r) and str(r.get("contract_date")).strip() == cd)
    if any("option_id" in r for r in rows):
        for oid in sorted({str(r.get("option_id")) for r in rows}):
            out[f"position,option_id={oid}"] = pc_from(rows, lambda r, oid=oid: pos(r) and str(r.get("option_id")) == oid)
    return out


def matches(pc, official) -> bool:
    return pc is not None and official is not None and f"{pc:.2f}" == f"{official:.2f}"


def _clip(v, n: int = 40) -> str:
    s = str(v)
    return s if len(s) <= n else s[:n] + "…"


# ════════════════════════════════════════════════════════════════
# 抓取與輸出
# ════════════════════════════════════════════════════════════════

def fetch_txo(d: str) -> list[dict] | None:
    from finmind import fm_get  # lazy：測試不碰網路
    df = fm_get("TaiwanOptionDaily", data_id="TXO", start_date=d, end_date=d)
    if df is None:
        return None
    return sentiment._records(df)


def fetch_taifex_raw() -> list:
    return sentiment._default_taifex()


def taifex_items_for(items, d: str) -> list[dict]:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        for k, v in it.items():
            if ("date" in str(k).lower() or "日期" in str(k)) and sentiment._taifex_date(v) == d:
                out.append(it)
                break
    return out


def report_day(d: str, rows: list[dict], taifex_items, p=print, holidays=None) -> None:
    p(f"\n{'=' * 72}\n# {d}")
    same = [r for r in rows if str(r.get("date") or d)[:10] == d]
    p(f"列數：全部 {len(rows)}、當日 {len(same)}（他日 {len(rows) - len(same)}）")
    if not same:
        return
    cols = sorted({k for r in same for k in r})
    p(f"欄位：{cols}")
    for i, r in enumerate(same[:3]):
        p(f"樣本 {i}: " + ", ".join(f"{k}={_clip(r.get(k))}" for k in cols))

    p("\n## session × call_put")
    for k, g in group_sums(same, lambda r: (_sess(r) or "<空>", str(r.get("call_put")))).items():
        p(f"  {k[0]:<14} {k[1]:<6} OI={int(g['oi']):>9,} Vol={int(g['vol']):>9,} n={g['n']}")

    p("\n## contract_date(kind) × session × call_put")
    for k, g in group_sums(same, lambda r: (str(r.get("contract_date")).strip(), contract_kind(r.get("contract_date")),
                                             _sess(r) or "<空>", str(r.get("call_put")))).items():
        tag = " ★週契約" if k[1] == "weekly" else (" ◆非6碼" if k[1] == "other" else "")
        p(f"  {k[0]:<14} {k[1]:<8} {k[2]:<14} {k[3]:<6} OI={int(g['oi']):>9,} Vol={int(g['vol']):>9,} n={g['n']}{tag}")

    if any("option_id" in r for r in same):
        p("\n## option_id × session × call_put")
        for k, g in group_sums(same, lambda r: (str(r.get("option_id")), _sess(r) or "<空>", str(r.get("call_put")))).items():
            p(f"  {k[0]:<10} {k[1]:<14} {k[2]:<6} OI={int(g['oi']):>9,} Vol={int(g['vol']):>9,} n={g['n']}")

    ex, bad = sentiment.expiring_contracts(same, d, holidays)
    ref = sentiment.pc_ratios(same, ex)
    p(f"\n## 當日到期契約（行事曆{'已載入' if holidays is not None else '未載入＝只排週末'}）："
      f"{sorted(ex) or '無'}｜無法解析 {bad}")
    p(f"## sentiment.pc_ratios（現行 cv{sentiment.SENTIMENT_CALC_VER}，排除當日到期）：pc_oi={ref['pc_oi']} "
      f"pc_vol={ref['pc_vol']} put_oi={ref['put_oi']} call_oi={ref['call_oi']} "
      f"put_vol={ref['put_vol']} call_vol={ref['call_vol']}")

    off = sentiment.parse_taifex_pc(taifex_items).get(d) if taifex_items is not None else None
    items = taifex_items_for(taifex_items, d) if taifex_items is not None else []
    p("\n## 期交所 OpenAPI PutCallRatio 同日")
    if taifex_items is None:
        p("  （取不到期交所資料）")
    elif not items:
        p("  （期交所回傳中無此日）")
    for it in items:
        p("  " + ", ".join(f"{k}={v}" for k, v in it.items()))
    off_oi = off.get("pc_oi") if off else None
    off_vol = off.get("pc_vol") if off else None
    p(f"  解析：PutCallOIRatio%={off_oi}  PutCallVolumeRatio%={off_vol}")

    p("\n## 候選口徑（OI；★＝與官方 PutCallOIRatio% 逐位相同）")
    for name, c in candidates(same, d, holidays).items():
        star = " ★" if matches(c["pc"], off_oi) else ""
        p(f"  {name:<44} put={c['put']:>9,} call={c['call']:>9,} P/C={c['pc']} n={c['n']}{star}")
    vol_c = pc_from(same, lambda r: _sess(r) in SESSIONS, field="volume")
    p(f"\n## 成交量（position+after_market）P/C={vol_c['pc']}"
      f"{' ★與官方相同' if matches(vol_c['pc'], off_vol) else ''}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dates", default=DEFAULT_DATES, help="逗號分隔 YYYY-MM-DD")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    dates = [s.strip() for s in a.dates.split(",") if s.strip()]
    bad = [s for s in dates if not YMD_RE.match(s)]
    if bad or not dates:
        print(f"日期格式錯誤：{bad or a.dates!r}")
        return 2
    try:
        taifex = fetch_taifex_raw()
        print(f"期交所 OpenAPI 回傳 {len(taifex) if isinstance(taifex, list) else type(taifex).__name__} 筆")
    except Exception as e:
        print(f"期交所 OpenAPI 取不到：{type(e).__name__}: {mask_secret(e)[:200]}")
        taifex = None
    holidays = twse_holidays.load()   # 同一班只讀一次；讀不到＝None＝只排週末
    print(f"休市行事曆：{'已載入' if holidays is not None else '未載入（只排週末）'}")
    rc = 0
    for d in dates:
        try:
            rows = fetch_txo(d)
            if rows is None:
                print(f"\n# {d}：TaiwanOptionDaily 請求失敗")
                rc = 1
                continue
            report_day(d, rows, taifex, holidays=holidays)
        except Exception as e:
            print(f"\n# {d}：例外 {type(e).__name__}: {mask_secret(e)[:300]}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
