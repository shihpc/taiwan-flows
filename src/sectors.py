# src/sectors.py
# 類股資金流（規格後新增功能）：在現有逐檔法人買賣超上，加「分類維度」。
#
# 兩種分類法 × 四種法人別 × 多時間窗：
#   classify : exchange（交易所產業別，互斥可加總）/ chain（產業鏈 industry_node，多對多）
#   investor : total（三大法人合計）/ foreign（外資）/ trust（投信）/ dealer（自營）
#   window   : 1d / r5 / r10 / r20 / r65（沿用 budget.py 的窗）
#
# 設計：
#   - 不重抓、不重算流量；重用 budget.load_daily + budget.aggregate（口徑已對驗 T86）。
#     total = f+t+d；金額單位千元(_k)、買賣超張(net_lots)，與專案一致。
#   - 交易所產業別來自 meta.stocks[code].industry（既有，免費）。
#   - 產業鏈來自 data/industry_chain.json（snapshot，--build-chain 產生；變動慢，不必每日抓）。
#
# 輸出（前端純讀，點類股 → 用 stocks 表 filter+sort 展開個股）：
#   data/sector_latest.json（單日）、data/sector_ranges.json（r5/r10/r20/r65）
#
# 口徑提醒（前端徽章需標示）：
#   - chain 是多對多：一檔掛多節點，各節點加總會重疊、≠大盤、不可讀成市佔（主題曝險）。
#   - chain 僅含產業鏈有分類之個股，不含 ETF/權證（與 ETF 頁口徑不同）。
#
# 用法：
#   python src/sectors.py --build-chain      # 抓 TaiwanStockIndustryChain → industry_chain.json（偶爾跑）
#   python src/sectors.py                    # 產 sector_latest.json + sector_ranges.json（每日）

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import budget  # noqa: E402  重用 load_daily / aggregate / WINDOWS
from budget import jround  # noqa: E402  與前端 Math.round 同語意（見 budget.jround）
from sanitize import sanitize_label  # noqa: E402  產業／次產業名咽喉點消毒

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sectors")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CHAIN_PATH = DATA / "industry_chain.json"
LATEST_PATH = DATA / "sector_latest.json"
RANGES_PATH = DATA / "sector_ranges.json"
TPE = timezone(timedelta(hours=8))

UNCLASSIFIED = "其他/未分類"
# 法人別 → (net 欄, amt 欄)；total 於 stock row 預先算好
INVESTORS = {
    "total": ("tot_net", "tot_amt"),
    "foreign": ("f_net", "f_amt"),
    "trust": ("t_net", "t_amt"),
    "dealer": ("d_net", "d_amt"),
}


# ════════════════════════════════════════════════════════════════
# 產業鏈 snapshot
# ════════════════════════════════════════════════════════════════

def build_chain_snapshot() -> dict:
    """抓 TaiwanStockIndustryChain（Sponsor 級），存成 code→節點對照。變動慢，偶爾跑。"""
    from finmind import fm_get
    logger.info("抓取 TaiwanStockIndustryChain …")
    df = fm_get("TaiwanStockIndustryChain")
    if df is None or df.empty:
        raise RuntimeError("TaiwanStockIndustryChain 抓取失敗（檢查 token 等級是否仍為 Sponsor）")
    df["stock_id"] = df["stock_id"].astype(str)
    cmap: dict[str, dict] = {}
    for _, r in df.iterrows():
        code = r["stock_id"]
        e = cmap.setdefault(code, {"i": [], "s": [], "p": []})
        ind, sub = sanitize_label(r["industry"]), sanitize_label(r["sub_industry"])
        if ind and ind not in e["i"]:
            e["i"].append(ind)
        if sub and sub not in e["s"]:
            e["s"].append(sub)
        # (industry, sub_industry) 配對：產業→次產業第二層 drill 用（前端需此配對）
        if ind and sub and [ind, sub] not in e["p"]:
            e["p"].append([ind, sub])
    industries = sorted({i for v in cmap.values() for i in v["i"]})
    snap = {
        "generated_at": datetime.now(TPE).isoformat(),
        "source": "FinMind TaiwanStockIndustryChain",
        "stock_count": len(cmap),
        "industry_count": len(industries),
        "industries": industries,
        "map": cmap,  # code -> {i:[industries], s:[sub_industries]}
    }
    CHAIN_PATH.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    logger.info(f"已寫入 industry_chain.json（{len(cmap)} 檔 / {len(industries)} 產業 / "
                f"{CHAIN_PATH.stat().st_size/1024:.0f} KB）")
    return snap


def load_chain_map() -> dict[str, list[str]]:
    """code -> [industry 節點]（產業鏈頁用 industry 層）。缺檔則回空，產業鏈頁會空。"""
    if not CHAIN_PATH.exists():
        logger.warning("找不到 industry_chain.json，請先跑 --build-chain；產業鏈頁將為空")
        return {}
    snap = json.loads(CHAIN_PATH.read_text(encoding="utf-8"))
    return {code: v.get("i", []) for code, v in snap["map"].items()}


# ════════════════════════════════════════════════════════════════
# 逐檔流量表（drill-down 用）＋ 類股摘要
# ════════════════════════════════════════════════════════════════

# 交易所產業別命名正規化：櫃買兩個「上櫃ETF」分類字串（舊長名/新短名）合併
EXCH_ALIAS = {"上櫃指數股票型基金(ETF)": "上櫃ETF"}


def _exch(industry) -> str:
    s = str(industry).strip()
    if not s or s.lower() == "nan":
        return UNCLASSIFIED
    return EXCH_ALIAS.get(s, s)


def stock_rows(agg: dict, chain_map: dict[str, list[str]]) -> list[dict]:
    """budget.aggregate 的逐檔結果 → 帶兩種分類標籤的精簡流量列。"""
    rows = []
    for code, a in agg.items():
        f_net, t_net, d_net = a["f_net"], a["t_net"], a["d_net"]
        f_amt, t_amt, d_amt = a["f_amt"], a["t_amt"], a["d_amt"]
        if (f_net or t_net or d_net) == 0 and (f_amt or t_amt or d_amt) == 0:
            continue  # 當窗完全無法人進出，省體積
        rows.append({
            "code": code, "name": a["name"], "is_etf": a["is_etf"],
            "exch": _exch(a["industry"]), "chain": chain_map.get(code, []),
            "close": a["close"], "chg_pct": a["chg_pct"],
            "f_net": f_net, "f_amt": f_amt, "t_net": t_net, "t_amt": t_amt,
            "d_net": d_net, "d_amt": d_amt,
            "tot_net": jround(f_net + t_net + d_net, 1), "tot_amt": jround(f_amt + t_amt + d_amt),
        })
    return rows


def summarize(rows: list[dict], classify: str, investor: str) -> list[dict]:
    """單一 (classify, investor) 的類股摘要，依買賣超金額排序。"""
    net_col, amt_col = INVESTORS[investor]
    buckets: dict[str, dict] = defaultdict(lambda: {"net_amt_k": 0, "net_lots": 0.0,
                                                    "n": 0, "n_buy": 0, "n_sell": 0})
    for r in rows:
        sectors = [r["exch"]] if classify == "exchange" else r["chain"]
        if not sectors:
            continue
        net, amt = r[net_col], r[amt_col]
        for s in sectors:
            b = buckets[s]
            b["net_amt_k"] += amt
            b["net_lots"] += net
            b["n"] += 1
            if net > 0:
                b["n_buy"] += 1
            elif net < 0:
                b["n_sell"] += 1
    out = [{"sector": s, **v, "net_lots": jround(v["net_lots"], 1)} for s, v in buckets.items()]
    # 次鍵 sector：金額同值（常見 0）時與前端 jPageSectors 給出同一排名
    out.sort(key=lambda x: (-x["net_amt_k"], x["sector"]))
    return out


def build_view(agg: dict, chain_map: dict[str, list[str]]) -> dict:
    rows = stock_rows(agg, chain_map)
    chain_cov = sum(1 for r in rows if r["chain"])
    classifications = {}
    for classify, many in (("exchange", False), ("chain", True)):
        classifications[classify] = {
            "many_to_many": many,
            "investors": {inv: summarize(rows, classify, inv) for inv in INVESTORS},
        }
    classifications["chain"]["coverage"] = {
        "stocks_with_chain": chain_cov, "stocks_total": len(rows),
        "excludes": "ETF/權證等無產業鏈分類者",
    }
    return {"classifications": classifications, "stocks": rows}


# ════════════════════════════════════════════════════════════════
# 主程式
# ════════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> None:
    # argv 可由呼叫端（run_daily / verify_daily）明確傳空清單，避免吃到上層的 CLI 參數
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-chain", action="store_true", help="(重)抓產業鏈 snapshot 後結束")
    args = ap.parse_args(argv)
    if args.build_chain:
        build_chain_snapshot()
        return

    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    dates, docs = budget.load_daily()
    if not dates:
        logger.error("無 daily 檔")
        return
    chain_map = load_chain_map()
    d2 = dates[-1]
    logger.info(f"最近交易日 {d2}，共 {len(dates)} 交易日；產業鏈對照 {len(chain_map)} 檔")

    # 單日
    latest = {"date": d2, "generated_at": datetime.now(TPE).isoformat(), "window": "1d",
              **build_view(budget.aggregate(dates, docs, meta, 1), chain_map)}
    LATEST_PATH.write_text(json.dumps(latest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    logger.info(f"已寫入 sector_latest.json（{LATEST_PATH.stat().st_size/1024:.0f} KB）")

    # 區間
    ranges = {"date": d2, "generated_at": datetime.now(TPE).isoformat(), "windows": {}}
    for key, n in budget.WINDOWS.items():
        win = dates[-n:] if n <= len(dates) else dates
        ranges["windows"][key] = {"trading_days": len(win), "start": win[0], "end": win[-1],
                                  **build_view(budget.aggregate(dates, docs, meta, n), chain_map)}
        logger.info(f"  {key}: {len(win)} 交易日（{win[0]} ~ {win[-1]}）")
    RANGES_PATH.write_text(json.dumps(ranges, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    logger.info(f"已寫入 sector_ranges.json（{RANGES_PATH.stat().st_size/1024:.0f} KB）")


if __name__ == "__main__":
    main()
