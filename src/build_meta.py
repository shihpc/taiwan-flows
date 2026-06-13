# src/build_meta.py
# 規格步驟 1：產生 data/meta.json
#
# meta.json = 全 pipeline 與前端共用的「字典檔」：
#   - 代號 ↔ 名稱
#   - 產業別（ETF 頁債券型判別、投信頁分類用）
#   - is_etf 旗標（代號 00 開頭）
#   - issued_lots 發行張數（取自 baseline；用於持股比率 = 庫存 ÷ 發行張數）
#   - calendar 交易日曆（初始為空，pipeline 逐日 append）
#
# 用法：
#   python src/build_meta.py
#
# 設計決策：
#   - 母體 = 4 位數一般股（[1-9]\d{3}）+ 00 開頭 ETF（含 00631L / 00400A 等字母後綴）
#     排除權證（6 位數 0xxxxx）、選擇權、興櫃等
#   - issued_lots 對齊：baseline 代號可能為 "50"，TaiwanStockInfo 為 "0050"
#     → 以原碼與去前導 0 兩種 key 建立 lookup

from __future__ import annotations

import json
import logging
import math
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finmind import fm_get  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_meta")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BASELINE = DATA / "baseline_20260430.json"
META_OUT = DATA / "meta.json"

TPE = timezone(timedelta(hours=8))

# 母體過濾：一般股 4 位數（非 0 開頭）或 00 開頭 ETF（可帶單一字母後綴）
RE_STOCK = re.compile(r"^[1-9]\d{3}$")
RE_ETF = re.compile(r"^00\d{2,4}[A-Z]?$")


def _load_baseline_lookup() -> dict[str, float]:
    """建立 code -> issued_lots 查表，同時放原碼與去前導 0 版本以利對齊。"""
    raw = json.loads(BASELINE.read_text(encoding="utf-8"))
    lookup: dict[str, float] = {}
    for row in raw["data"]:
        code = str(row["code"]).strip()
        lots = row.get("issued_lots")
        if lots is None or (isinstance(lots, float) and math.isnan(lots)):
            continue
        lookup[code] = float(lots)
        stripped = code.lstrip("0") or "0"
        lookup.setdefault(stripped, float(lots))
    return lookup


def _match_issued_lots(code: str, lookup: dict[str, float]) -> float | None:
    """code 先試原碼，再試去前導 0。"""
    if code in lookup:
        return lookup[code]
    stripped = code.lstrip("0") or "0"
    return lookup.get(stripped)


def build_meta() -> dict:
    logger.info("抓取 TaiwanStockInfo …")
    df = fm_get("TaiwanStockInfo")
    if df is None or df.empty:
        raise RuntimeError("TaiwanStockInfo 抓取失敗")
    df["stock_id"] = df["stock_id"].astype(str)
    # 同一代號可能多列（不同 industry_category），保留第一列
    df = df.drop_duplicates(subset="stock_id", keep="first")

    lookup = _load_baseline_lookup()

    stocks: dict[str, dict] = {}
    matched = 0
    for _, r in df.iterrows():
        code = r["stock_id"]
        is_etf = bool(RE_ETF.match(code))
        if not (is_etf or RE_STOCK.match(code)):
            continue
        lots = _match_issued_lots(code, lookup)
        if lots is not None:
            matched += 1
        stocks[code] = {
            "name": str(r["stock_name"]),
            "industry": str(r["industry_category"]),
            "is_etf": is_etf,
            "issued_lots": lots,
        }

    n_etf = sum(1 for v in stocks.values() if v["is_etf"])
    logger.info(f"母體 {len(stocks)} 檔（ETF {n_etf}、一般股 {len(stocks) - n_etf}）")
    logger.info(f"issued_lots 對齊成功 {matched}/{len(stocks)} 檔")

    return {
        "generated_at": datetime.now(TPE).isoformat(),
        "baseline_date": json.loads(BASELINE.read_text(encoding="utf-8"))["baseline_date"],
        "source": "FinMind TaiwanStockInfo + baseline issued_lots",
        "count": len(stocks),
        "etf_count": n_etf,
        "stocks": stocks,
        # 保留既有 calendar（pipeline 逐日 append），重建 meta 不該清掉歷史交易日
        "calendar": json.loads(META_OUT.read_text(encoding="utf-8")).get("calendar", [])
        if META_OUT.exists() else [],
    }


def main() -> None:
    meta = build_meta()
    META_OUT.write_text(
        json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    size_kb = META_OUT.stat().st_size / 1024
    logger.info(f"已寫入 {META_OUT.relative_to(ROOT)}（{size_kb:.0f} KB）")


if __name__ == "__main__":
    main()
