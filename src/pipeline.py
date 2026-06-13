# src/pipeline.py
# 規格步驟 2：單日 fetch → 清洗 → 投信庫存累計 → 輸出 data/daily/YYYYMMDD.json
#
# 用法：
#   python src/pipeline.py --date 2026-06-11      # 指定交易日
#   python src/pipeline.py                         # 預設今天（非交易日/資料未更新則跳過）
#
# daily/YYYYMMDD.json 緊湊 schema（陣列式，省體積）：
#   cols = [code,close,chg_pct,vol,amt,t_net,t_amt,f_net,f_amt,d_net,d_amt,t_inv,f_shares,f_pct]
#   單位：張(net/inv/shares)、千元(amt)、%(pct)
#
# 三大法人類別映射（FinMind TaiwanStockInstitutionalInvestorsBuySell）：
#   外資 f = Foreign_Investor + Foreign_Dealer_Self
#   投信 t = Investment_Trust
#   自營 d = Dealer_self + Dealer_Hedging + Dealer
#
# 金額口徑：FinMind 法人資料只有張數（股），無金額欄
#   → 買賣超金額(千元) = 買賣超張 × 收盤價（與 CMoney 以收盤價估算口徑一致）

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finmind import fm_get  # noqa: E402
from futures import fetch_futures  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("pipeline")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DAILY_DIR = DATA / "daily"
META_PATH = DATA / "meta.json"
BASELINE_PATH = DATA / "baseline_20260430.json"

TPE = timezone(timedelta(hours=8))

COLS = ["code", "close", "chg_pct", "vol", "amt",
        "t_net", "t_amt", "f_net", "f_amt", "d_net", "d_amt",
        "t_inv", "f_shares", "f_pct"]

FOREIGN_NAMES = {"Foreign_Investor", "Foreign_Dealer_Self"}
TRUST_NAMES = {"Investment_Trust"}
DEALER_NAMES = {"Dealer_self", "Dealer_Hedging", "Dealer"}


# ════════════════════════════════════════════════════════════════
# meta / baseline
# ════════════════════════════════════════════════════════════════

def load_meta() -> dict:
    return json.loads(META_PATH.read_text(encoding="utf-8"))


def baseline_inventory() -> dict[str, float]:
    """baseline 投信庫存（張），key 同時放原碼與去前導 0。"""
    raw = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    inv: dict[str, float] = {}
    for row in raw["data"]:
        code = str(row["code"]).strip()
        val = float(row.get("trust_inv") or 0.0)
        inv[code] = val
        inv.setdefault(code.lstrip("0") or "0", val)
    return inv


def _baseline_lookup(inv: dict[str, float], code: str) -> float:
    if code in inv:
        return inv[code]
    return inv.get(code.lstrip("0") or "0", 0.0)


# ════════════════════════════════════════════════════════════════
# 庫存累計：取得 target 前一交易日的每檔投信庫存
# ════════════════════════════════════════════════════════════════

def running_inventory(target: str) -> dict[str, float]:
    """
    回傳「target 前一交易日收盤」的每檔投信庫存（張）。
    規則：自 baseline(2026-04-30) 起，依日序重放各 daily 檔的 t_inv。
    baseline 之前無意義，回空 dict。
    """
    base = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    base_date = base["baseline_date"]
    if target <= base_date:
        return {}

    inv = baseline_inventory()
    # 依日序套用 baseline 之後、target 之前的 daily 檔，最後一筆即為前一交易日庫存
    for f in sorted(DAILY_DIR.glob("*.json")):
        d = f.stem  # YYYYMMDD
        d_iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        if d_iso <= base_date or d_iso >= target:
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        ci = doc["cols"].index("t_inv")
        cc = doc["cols"].index("code")
        for row in doc["rows"]:
            if row[ci] is not None:
                inv[str(row[cc])] = float(row[ci])
    return inv


# ════════════════════════════════════════════════════════════════
# 抓取單日三 dataset
# ════════════════════════════════════════════════════════════════

def fetch_day(d: str):
    """抓 price / institutional / shareholding。price 空 → 非交易日或未更新。"""
    price = fm_get("TaiwanStockPrice", start_date=d, end_date=d)
    if price is None:
        raise RuntimeError(f"TaiwanStockPrice {d} 抓取失敗（API 錯誤）")
    if price.empty:
        return None  # 非交易日 / 資料尚未更新
    inst = fm_get("TaiwanStockInstitutionalInvestorsBuySell", start_date=d, end_date=d)
    share = fm_get("TaiwanStockShareholding", start_date=d, end_date=d)
    return price, (inst if inst is not None else pd.DataFrame()), \
        (share if share is not None else pd.DataFrame())


# ════════════════════════════════════════════════════════════════
# 組裝單日逐檔列
# ════════════════════════════════════════════════════════════════

def _agg_institutional(inst: pd.DataFrame) -> dict[str, dict]:
    """long → {code: {f_net, t_net, d_net}}（張）。buy/sell 單位為股 → /1000。"""
    out: dict[str, dict] = {}
    if inst.empty:
        return out
    inst = inst.copy()
    inst["stock_id"] = inst["stock_id"].astype(str)
    inst["net"] = (pd.to_numeric(inst["buy"], errors="coerce").fillna(0)
                   - pd.to_numeric(inst["sell"], errors="coerce").fillna(0)) / 1000.0
    for name_set, key in [(FOREIGN_NAMES, "f_net"), (TRUST_NAMES, "t_net"), (DEALER_NAMES, "d_net")]:
        sub = inst[inst["name"].isin(name_set)].groupby("stock_id")["net"].sum()
        for code, v in sub.items():
            out.setdefault(code, {"f_net": 0.0, "t_net": 0.0, "d_net": 0.0})[key] = round(float(v), 1)
    return out


def _shareholding_map(share: pd.DataFrame) -> dict[str, dict]:
    """{code: {f_shares(張), f_pct(%)}}。"""
    out: dict[str, dict] = {}
    if share.empty:
        return out
    share = share.copy()
    share["stock_id"] = share["stock_id"].astype(str)
    for _, r in share.iterrows():
        shares = pd.to_numeric(r.get("ForeignInvestmentShares"), errors="coerce")
        pct = pd.to_numeric(r.get("ForeignInvestmentSharesRatio"), errors="coerce")
        out[r["stock_id"]] = {
            "f_shares": round(float(shares) / 1000.0, 1) if pd.notna(shares) else None,
            "f_pct": round(float(pct), 2) if pd.notna(pct) else None,
        }
    return out


def build_rows(d: str, dfs, prev_inv: dict[str, float], meta: dict) -> list:
    price, inst, share = dfs
    base_date = meta["baseline_date"]
    inv_valid = d > base_date

    price = price.copy()
    price["stock_id"] = price["stock_id"].astype(str)
    inst_map = _agg_institutional(inst)
    share_map = _shareholding_map(share)
    stocks = meta["stocks"]

    rows = []
    for _, r in price.iterrows():
        code = r["stock_id"]
        if code not in stocks:  # 只收母體（一般股 + ETF），排除權證等
            continue
        close = pd.to_numeric(r["close"], errors="coerce")
        spread = pd.to_numeric(r["spread"], errors="coerce")
        vol_sh = pd.to_numeric(r["Trading_Volume"], errors="coerce")
        amt_yuan = pd.to_numeric(r["Trading_money"], errors="coerce")
        if pd.isna(close):
            continue
        prev_close = close - spread if pd.notna(spread) else None
        chg_pct = round(float(spread) / float(prev_close) * 100, 2) \
            if prev_close not in (None, 0) and pd.notna(spread) else 0.0

        inst_d = inst_map.get(code, {"f_net": 0.0, "t_net": 0.0, "d_net": 0.0})
        t_net, f_net, d_net = inst_d["t_net"], inst_d["f_net"], inst_d["d_net"]

        # 庫存累計：inv(t) = max(0, inv(t-1) + t_net)，下限 0
        # prev_inv 已以 baseline 種子，缺檔（baseline 未收錄的新股）以 0 起算
        if inv_valid:
            t_inv = round(max(0.0, prev_inv.get(code, _baseline_lookup(prev_inv, code)) + t_net), 1)
        else:
            t_inv = None

        sh = share_map.get(code, {"f_shares": None, "f_pct": None})

        rows.append([
            code,
            round(float(close), 2),
            chg_pct,
            int(round(float(vol_sh) / 1000.0)) if pd.notna(vol_sh) else 0,
            int(round(float(amt_yuan) / 1000.0)) if pd.notna(amt_yuan) else 0,
            t_net, int(round(t_net * float(close))),
            f_net, int(round(f_net * float(close))),
            d_net, int(round(d_net * float(close))),
            t_inv,
            sh["f_shares"], sh["f_pct"],
        ])
    rows.sort(key=lambda x: x[0])
    return rows


# ════════════════════════════════════════════════════════════════
# meta calendar
# ════════════════════════════════════════════════════════════════

def append_calendar(d: str, meta: dict) -> None:
    cal = set(meta.get("calendar", []))
    cal.add(d)
    meta["calendar"] = sorted(cal)
    meta["generated_at"] = datetime.now(TPE).isoformat()
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════════

def run_date(d: str) -> bool:
    """跑單一交易日。回傳 True=已產出，False=非交易日/未更新跳過。"""
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    meta = load_meta()

    logger.info(f"抓取 {d} 三大 dataset …")
    dfs = fetch_day(d)
    if dfs is None:
        logger.warning(f"{d} 無股價資料（非交易日或 FinMind 尚未更新），跳過")
        return False

    prev_inv = running_inventory(d)
    rows = build_rows(d, dfs, prev_inv, meta)
    if not rows:
        logger.warning(f"{d} 組裝後無有效列，跳過")
        return False

    out = {"date": d, "cols": COLS, "rows": rows}
    out_path = DAILY_DIR / f"{d.replace('-', '')}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    append_calendar(d, meta)

    size_kb = out_path.stat().st_size / 1024
    n_inv = sum(1 for r in rows if r[COLS.index("t_inv")] is not None)
    logger.info(f"已寫入 {out_path.relative_to(ROOT)}（{len(rows)} 檔, {size_kb:.0f} KB, t_inv 有效 {n_inv}）")

    # 期貨三大法人（外資頁台指期卡用）— 非致命，缺漏不影響當日 daily
    try:
        if fetch_futures(d) is not None:
            logger.info(f"已存期貨 data/futures/{d.replace('-', '')}.json")
        else:
            logger.warning(f"{d} 期貨資料尚未更新，略過")
    except Exception as e:
        logger.warning(f"{d} 期貨抓取失敗（略過）：{e}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="交易日 YYYY-MM-DD（預設今天）")
    args = ap.parse_args()
    d = args.date or datetime.now(TPE).date().isoformat()
    run_date(d)


if __name__ == "__main__":
    main()
