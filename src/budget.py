# src/budget.py
# 規格步驟 4：預算四頁 → latest.json（單日）+ latest_ranges.json（近 5/10/20/65 交易日）
#
# 四頁（規格 4.2~4.5）：
#   etf     ETF 頁：整體統計卡 + 成交金額 Top20 + 市值 Top20
#   trust   投信進出：買超/賣超 各 Top30，排行A(持股變動率)/排行B(買賣超金額)
#   foreign 外資進出：同上 + 台指期外資未平倉卡
#   sync    同步買賣超：同步買/賣 Top30 + 成交量佔比 Top10
#
# 區間聚合口徑（規格 4.1）：
#   買賣超張/金額 = Σ；庫存/持股/比率 = d2 末日值；持股變動% = Σ買賣超張÷發行張數
#   漲跌% = close(d2)/close(d1前一交易日)−1；佔成交量 = Σ買賣超張÷Σ成交量；乖離月線% = d2 末日值
#
# 已知限制（daily schema 僅存 net，不存買/賣分拆，且無自營庫存）：
#   - ETF 頁成交金額排行用「法人淨額」而非買/賣分拆金額
#   - ETF 頁市值排行的「其他持股市值」= 市值 −(外資+投信)持股市值（自營庫存無資料）

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from futures import tx_foreign_oi  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("budget")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DAILY_DIR = DATA / "daily"
META_PATH = DATA / "meta.json"
LATEST_PATH = DATA / "latest.json"
RANGES_PATH = DATA / "latest_ranges.json"

TPE = timezone(timedelta(hours=8))
MA_PERIOD = 20  # 乖離月線
WINDOWS = {"r5": 5, "r10": 10, "r20": 20, "r65": 65}


def jround(v: float, nd: int = 0):
    """與前端 JS `Math.round` 同語意的四捨五入（half-up；負數的 .5 向 +∞）。

    Python 內建 `round()` 是 banker's rounding（half-to-even），`round(36.5)==36`
    而 JS `Math.round(36.5)===37`。買賣金額＝Σ(張×收盤) 的小數位常恰好落在 .5
    （張數 1 位、收盤 2 位），實測每個視窗有數十檔 `*_amt` 命中 → 同一檔同一數字
    在「單日/近N日」（後端預算）與「自訂區間/本週/上週/上月」（前端聚合）會差 1 千元。
    統一採 JS 語意（tests/parity.py 守門）。
    """
    f = 10 ** nd
    r = math.floor(v * f + 0.5) / f
    return int(r) if nd == 0 else r


# ════════════════════════════════════════════════════════════════
# 載入
# ════════════════════════════════════════════════════════════════

def load_daily() -> tuple[list[str], dict[str, dict]]:
    """回傳 (升序日期, {date: {code: rowdict}})。"""
    docs: dict[str, dict] = {}
    for f in sorted(DAILY_DIR.glob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        cols = doc["cols"]
        docs[doc["date"]] = {r[0]: dict(zip(cols, r)) for r in doc["rows"]}
    return sorted(docs), docs


def close_series(dates: list[str], docs: dict, code: str) -> list[float]:
    out = []
    for d in dates:
        row = docs[d].get(code)
        if row and row["close"] is not None:
            out.append(float(row["close"]))
    return out


# ════════════════════════════════════════════════════════════════
# 區間聚合：window [d1..d2]，回傳 {code: agg}
# ════════════════════════════════════════════════════════════════

def aggregate(dates: list[str], docs: dict, meta: dict, n: int) -> dict[str, dict]:
    d2 = dates[-1]
    win = dates[-n:] if n <= len(dates) else dates
    d1 = win[0]
    i1 = dates.index(d1)
    prev = dates[i1 - 1] if i1 > 0 else None  # d1 前一交易日（算漲跌%）
    # 乖離月線的取樣區間＝「d2 結尾的最近 MA_PERIOD 個交易日」。
    # 與前端 aggregateRange 的 ma20dates（cal.slice(i2-19, i2+1)）完全一致；
    # 原本用「所有 ≤d2 的交易日取最後 20 個有效收盤」，遇停牌/新上市會往更早的
    # 日子補樣本，導致 r20 與自訂區間對同一檔算出不同 bias20（見 tests/parity）。
    ma_dates = dates[-MA_PERIOD:]
    stocks = meta["stocks"]

    agg: dict[str, dict] = {}
    for code, info in stocks.items():
        end = docs[d2].get(code)
        if end is None or end["close"] is None:
            continue
        sums = {"t_net": 0.0, "t_amt": 0.0, "f_net": 0.0, "f_amt": 0.0,
                "d_net": 0.0, "d_amt": 0.0, "vol": 0.0, "amt": 0.0,
                "f_buy": 0.0, "f_sell": 0.0, "t_buy": 0.0, "t_sell": 0.0, "d_buy": 0.0, "d_sell": 0.0}
        # 買/賣金額（千元）＝Σ 逐日 買賣張 × 當日收盤（與 t_amt 同口徑，分母分子一致）
        amt_acc = {"f_buy_amt": 0.0, "f_sell_amt": 0.0, "t_buy_amt": 0.0,
                   "t_sell_amt": 0.0, "d_buy_amt": 0.0, "d_sell_amt": 0.0}
        for d in win:
            row = docs[d].get(code)
            if not row:
                continue
            for k in sums:
                v = row.get(k)
                if v is not None:
                    sums[k] += v
            cl = row.get("close")
            if cl is not None:
                for tag in ("f", "t", "d"):
                    for sd in ("buy", "sell"):
                        lots = row.get(f"{tag}_{sd}")
                        if lots is not None:
                            amt_acc[f"{tag}_{sd}_amt"] += lots * cl

        close2 = float(end["close"])
        # 漲跌%
        base_close = None
        if prev and docs[prev].get(code) and docs[prev][code]["close"] is not None:
            base_close = float(docs[prev][code]["close"])
        elif docs[d1].get(code) and docs[d1][code]["close"] is not None:
            base_close = float(docs[d1][code]["close"])
        # 寫法固定為 `c/base*100-100`（不是 `(c/base-1)*100`）：兩者數學等價但 IEEE754
        # 誤差不同，(c/base-1)*100 會把 240→226.5 算成 -5.625000000000002，四捨五入後
        # 變 -5.63 而前端得 -5.62。與 index.html aggregateRange 保持同一寫法。
        chg = jround(close2 / base_close * 100 - 100, 2) if base_close else 0.0
        # 乖離月線%（均價為 0，如長期未成交股，給 None 避免除以零崩潰）
        cs = close_series(ma_dates, docs, code)
        ma = (sum(cs) / len(cs)) if cs else 0
        bias = jround((close2 / ma - 1) * 100, 2) if ma else None

        issued = info.get("issued_lots")
        if issued is not None and (not isinstance(issued, (int, float)) or math.isnan(issued)):
            issued = None
        agg[code] = {
            "code": code, "name": info["name"], "is_etf": info["is_etf"],
            "industry": info["industry"], "issued_lots": issued, "close": jround(close2, 2),
            "chg_pct": chg, "bias20": bias,
            "vol": jround(sums["vol"]), "amt": jround(sums["amt"]),
            "t_net": jround(sums["t_net"], 1), "t_amt": jround(sums["t_amt"]),
            "f_net": jround(sums["f_net"], 1), "f_amt": jround(sums["f_amt"]),
            "d_net": jround(sums["d_net"], 1), "d_amt": jround(sums["d_amt"]),
            "t_inv": end.get("t_inv"), "f_shares": end.get("f_shares"), "f_pct": end.get("f_pct"),
            "f_buy": jround(sums["f_buy"], 1), "f_sell": jround(sums["f_sell"], 1),
            "t_buy": jround(sums["t_buy"], 1), "t_sell": jround(sums["t_sell"], 1),
            "d_buy": jround(sums["d_buy"], 1), "d_sell": jround(sums["d_sell"], 1),
            "f_buy_amt": jround(amt_acc["f_buy_amt"]), "f_sell_amt": jround(amt_acc["f_sell_amt"]),
            "t_buy_amt": jround(amt_acc["t_buy_amt"]), "t_sell_amt": jround(amt_acc["t_sell_amt"]),
            "d_buy_amt": jround(amt_acc["d_buy_amt"]), "d_sell_amt": jround(amt_acc["d_sell_amt"]),
        }
    return agg


# ════════════════════════════════════════════════════════════════
# 衍生欄位
# ════════════════════════════════════════════════════════════════

def _ratio(num, den):
    return jround(num / den * 100, 2) if den else None


def _inst_row(a: dict, side: str) -> dict:
    """投信(t)/外資(f) 進出頁共用列。side='t' or 'f'。"""
    net = a[f"{side}_net"]
    amt = a[f"{side}_amt"]
    issued = a["issued_lots"]
    if side == "t":
        hold = a["t_inv"]
        hold_pct = _ratio(hold, issued) if (hold is not None and issued) else None
    else:
        hold = a["f_shares"]
        hold_pct = a["f_pct"]
    chg_hold_pct = _ratio(net, issued) if issued else None  # 持股變動%
    hold_value_m = jround(hold * a["close"] / 1000) if hold is not None else None  # 百萬
    return {
        "code": a["code"], "name": a["name"],
        "net_amt_k": amt, "net_lots": net,
        "chg_hold_pct": chg_hold_pct, "hold_pct": hold_pct,
        "hold_lots": hold, "hold_value_m": hold_value_m,
        "vol_ratio": _ratio(net, a["vol"]),  # 法人佔成交量
        "chg_pct": a["chg_pct"], "bias20": a["bias20"],
    }


def page_inst(agg: dict, side: str) -> dict:
    """投信/外資進出頁：買超/賣超 各 Top30，排行 A(持股變動率)/B(買賣超金額)。

    所有排行的排序鍵都帶次鍵 `code`：主鍵同值時（佔成交量常見整數比，如 1.0）
    若不指定次鍵，排名取決於語言的走訪順序——Python dict 依 meta.stocks 插入序，
    JS 物件則把「像整數的鍵」（2330）排在字串鍵（00637L）之前並依數值遞增。
    同一檔資料在後端預算與前端聚合會選出不同的 Top30 成員（非只是順序）。
    """
    rows = [a for a in agg.values() if a[f"{side}_net"] != 0]
    buy = [r for r in rows if r[f"{side}_net"] > 0]
    sell = [r for r in rows if r[f"{side}_net"] < 0]

    def topA(lst):  # 持股變動率（|net|÷發行張數）
        scored = [r for r in lst if r["issued_lots"]]
        scored.sort(key=lambda a: (-abs(a[f"{side}_net"]) / a["issued_lots"], a["code"]))
        return [_inst_row(a, side) for a in scored[:30]]

    def topB(lst):  # 買賣超金額
        lst = sorted(lst, key=lambda a: (-abs(a[f"{side}_amt"]), a["code"]))
        return [_inst_row(a, side) for a in lst[:30]]

    def topV(lst):  # 佔成交量（|net|÷成交量）
        scored = [r for r in lst if r["vol"]]
        scored.sort(key=lambda a: (-abs(a[f"{side}_net"]) / a["vol"], a["code"]))
        return [_inst_row(a, side) for a in scored[:30]]

    return {
        "buy_by_chg": topA(buy), "buy_by_amt": topB(buy), "buy_by_vol": topV(buy),
        "sell_by_chg": topA(sell), "sell_by_amt": topB(sell), "sell_by_vol": topV(sell),
    }


def page_etf(agg: dict) -> dict:
    etfs = [a for a in agg.values() if a["is_etf"]]

    def is_bond(a):  # 債券/固收/平衡型：名稱含「債」或 代號結尾 B(債券)/D(主動債)/T(平衡)
        return a["code"][-1] in ("B", "D", "T") or "債" in a["name"]

    def mktcap_k(a):
        return jround(a["issued_lots"] * a["close"]) if a["issued_lots"] else 0

    def stats(group):
        return {
            "count": len(group),
            "mktcap_k": sum(mktcap_k(a) for a in group),
            "turnover_k": sum(a["amt"] for a in group),
            "f_amt_k": sum(a["f_amt"] for a in group),
            "t_amt_k": sum(a["t_amt"] for a in group),
            "d_amt_k": sum(a["d_amt"] for a in group),
            "other_amt_k": -sum(a["f_amt"] + a["t_amt"] + a["d_amt"] for a in group),
        }

    bond = [a for a in etfs if is_bond(a)]
    nonbond = [a for a in etfs if not is_bond(a)]

    def share(a, tag):  # 法人佔成交比重 = (買張+賣張)/(2×成交張)
        v = a["vol"]
        return jround((a[f"{tag}_buy"] + a[f"{tag}_sell"]) / (2 * v) * 100, 2) if v else None

    def shares(a):
        return {"f_share": share(a, "f"), "t_share": share(a, "t"), "d_share": share(a, "d")}

    def turnover_row(a):
        return {"code": a["code"], "name": a["name"], "turnover_k": a["amt"],
                "f_amt_k": a["f_amt"], "t_amt_k": a["t_amt"], "d_amt_k": a["d_amt"],
                "other_amt_k": -(a["f_amt"] + a["t_amt"] + a["d_amt"]),
                "f_buy_k": a["f_buy_amt"], "f_sell_k": a["f_sell_amt"],
                "t_buy_k": a["t_buy_amt"], "t_sell_k": a["t_sell_amt"],
                "d_buy_k": a["d_buy_amt"], "d_sell_k": a["d_sell_amt"],
                **shares(a), "chg_pct": a["chg_pct"]}

    def mktcap_row(a):
        mc = mktcap_k(a)
        f_val = jround(a["f_shares"] * a["close"]) if a["f_shares"] is not None else 0
        pct = lambda v: jround(v / mc * 100, 2) if mc else None  # noqa: E731
        # 佝比＝持股市值/總市值。外資官方準；投信對 ETF 失真、自營無來源 → 一律 None（顯「—」）。
        # 其他＝市值−外資持股市值（含投信/自營/散戶等，因投信無法可靠拆分）。
        return {"code": a["code"], "name": a["name"], "mktcap_k": mc,
                "f_hold_value_k": f_val, "t_hold_value_k": None,
                "other_value_k": mc - f_val,
                "f_share": pct(f_val), "t_share": None, "d_share": None,
                "chg_pct": a["chg_pct"]}

    def top(lst, key):  # 次鍵 code：同值時與前端選出同一批（見 page_inst docstring）
        return sorted(lst, key=lambda a: (-key(a), a["code"]))[:20]

    return {
        "stats": {"all": stats(etfs), "nonbond": stats(nonbond), "bond": stats(bond)},
        # 股票型(非B) / 債券型(B結尾) 分表
        "turnover_stock": [turnover_row(a) for a in top(nonbond, lambda a: a["amt"])],
        "turnover_bond": [turnover_row(a) for a in top(bond, lambda a: a["amt"])],
        "mktcap_stock": [mktcap_row(a) for a in top(nonbond, mktcap_k)],
        "mktcap_bond": [mktcap_row(a) for a in top(bond, mktcap_k)],
    }


def page_sync(agg: dict) -> dict:
    rows = list(agg.values())
    sync_buy = [a for a in rows if a["t_net"] > 0 and a["f_net"] > 0]
    sync_sell = [a for a in rows if a["t_net"] < 0 and a["f_net"] < 0]

    def sync_row(a):
        # 同步強度 = 雙方金額較小者（兩邊都大買/大賣才算真共識，對作頁的鏡像）
        strength = min(abs(a["t_amt"]), abs(a["f_amt"]))
        return {"code": a["code"], "name": a["name"],
                "t_amt_k": a["t_amt"], "t_net": a["t_net"],
                "f_amt_k": a["f_amt"], "f_net": a["f_net"],
                "sum_amt_k": a["t_amt"] + a["f_amt"], "sum_net": jround(a["t_net"] + a["f_net"], 1),
                "strength_k": strength, "chg_pct": a["chg_pct"]}

    # 依同步強度排序（雙方都重押的真共識優先；與對作頁一致）
    sb = sorted((sync_row(a) for a in sync_buy), key=lambda r: (-r["strength_k"], r["code"]))[:30]
    ss = sorted((sync_row(a) for a in sync_sell), key=lambda r: (-r["strength_k"], r["code"]))[:30]

    return {"sync_buy": sb, "sync_sell": ss}


def page_oppose(agg: dict) -> dict:
    """外資與投信對作頁：兩者反方向（一買一賣）。同步頁的鏡像。"""
    rows = list(agg.values())
    f_buy_t_sell = [a for a in rows if a["f_net"] > 0 and a["t_net"] < 0]  # 外資買·投信賣
    f_sell_t_buy = [a for a in rows if a["f_net"] < 0 and a["t_net"] > 0]  # 外資賣·投信買

    def row(a):
        # 對作強度 = 雙方金額較小者（兩邊都重押才算真分歧）
        strength = min(abs(a["t_amt"]), abs(a["f_amt"]))
        return {"code": a["code"], "name": a["name"],
                "t_amt_k": a["t_amt"], "t_net": a["t_net"],
                "f_amt_k": a["f_amt"], "f_net": a["f_net"],
                "strength_k": strength, "chg_pct": a["chg_pct"]}

    # 依對作強度排序（雙方都重押的真分歧優先）
    fb = sorted((row(a) for a in f_buy_t_sell), key=lambda r: (-r["strength_k"], r["code"]))[:30]
    fs = sorted((row(a) for a in f_sell_t_buy), key=lambda r: (-r["strength_k"], r["code"]))[:30]
    return {"f_buy_t_sell": fb, "f_sell_t_buy": fs}


# ════════════════════════════════════════════════════════════════
# 外資頁台指期未平倉卡
# ════════════════════════════════════════════════════════════════

def _prev_month_end(dates: list[str], d2: str) -> str | None:
    """d2 之前、屬於上一個月的最後一個交易日。"""
    m = d2[:7]
    earlier = [d for d in dates if d[:7] < m]
    return earlier[-1] if earlier else None


def foreign_futures_card(dates: list[str], d2: str) -> dict | None:
    cur = tx_foreign_oi(d2)
    if cur is None:
        return None
    pme = _prev_month_end(dates, d2)
    prev = tx_foreign_oi(pme) if pme else None
    card = {"date": d2, "oi_net_lots": cur["oi_net_lots"], "oi_net_amount_k": cur["oi_net_amount_k"]}
    if prev:
        card["vs_prev_month_lots"] = cur["oi_net_lots"] - prev["oi_net_lots"]
        card["prev_month_end"] = pme
    return card


# ════════════════════════════════════════════════════════════════
# 組頁
# ════════════════════════════════════════════════════════════════

def build_view(dates: list[str], docs: dict, meta: dict, n: int) -> dict:
    agg = aggregate(dates, docs, meta, n)
    d2 = dates[-1]
    return {
        "etf": page_etf(agg),
        "trust": page_inst(agg, "t"),
        "foreign": {**page_inst(agg, "f"), "futures_card": foreign_futures_card(dates, d2)},
        "sync": page_sync(agg),
        "oppose": page_oppose(agg),
    }


def main(argv: list[str] | None = None) -> None:
    # argv 可由呼叫端（run_daily / verify_daily）明確傳空清單，避免吃到上層的 CLI 參數
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-futures", action="store_true", help="略過期貨卡（離線測試用）")
    args = ap.parse_args(argv)
    if args.no_futures:
        global foreign_futures_card
        foreign_futures_card = lambda *a, **k: None  # noqa: E731

    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    dates, docs = load_daily()
    if not dates:
        logger.error("無 daily 檔")
        return
    d2 = dates[-1]
    logger.info(f"最近交易日 {d2}，共 {len(dates)} 交易日")

    # latest.json：單日
    latest = {"date": d2, "generated_at": datetime.now(TPE).isoformat(), "window": "1d",
              "pages": build_view(dates, docs, meta, 1)}
    LATEST_PATH.write_text(json.dumps(latest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    logger.info(f"已寫入 latest.json（{LATEST_PATH.stat().st_size/1024:.0f} KB）")

    # latest_ranges.json：近 5/10/20/65
    ranges = {"date": d2, "generated_at": datetime.now(TPE).isoformat(), "windows": {}}
    for key, n in WINDOWS.items():
        win = dates[-n:] if n <= len(dates) else dates
        ranges["windows"][key] = {"trading_days": len(win), "start": win[0], "end": win[-1],
                                  "pages": build_view(dates, docs, meta, n)}
        logger.info(f"  {key}: {len(win)} 交易日（{win[0]} ~ {win[-1]}）")
    RANGES_PATH.write_text(json.dumps(ranges, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    logger.info(f"已寫入 latest_ranges.json（{RANGES_PATH.stat().st_size/1024:.0f} KB）")


if __name__ == "__main__":
    main()
