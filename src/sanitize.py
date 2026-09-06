#!/usr/bin/env python3
"""管線咽喉點的標籤消毒（股名／產業名／產業鏈節點名）。

背景（2026-09-06）：FinMind 回來的 stock_name／industry_category／產業鏈節點名會原樣寫進
meta.json → latest.json／sector_*.json → index.html 以 innerHTML 拼字串顯示。前端已在格式器層
補 esc()，這裡是第二道防線——即使前端某處漏 esc，資料檔本身也不含可組成標籤／屬性的字元。

規則（純函式，無 I/O）：
  - 去除 < > " '（HTML 標籤與屬性邊界字元）與 C0/C1 控制字元；
  - `&` 刻意保留：真實資料有 14 檔 ETF 名含 `S&P`（如 00646 元大S&P500）與產業鏈節點
    「MR Headset & SG」（2026-09-06 以 data/meta.json、data/industry_chain.json 實查），拿掉會改到
    真實名稱；bare `&` 無法單獨組成標籤，前端 esc() 會轉 &amp;。
  - 首尾空白 strip，超過 MAX_LABEL_LEN（40）截斷（現行最長標籤恰 40 字，實查不受影響）。
  - 有動到內容時記一則 warning（含原字串 repr），方便事後追查來源。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

MAX_LABEL_LEN = 40
_BAD = re.compile(r"[<>\"'\x00-\x1f\x7f-\x9f]")


def sanitize_label(s, max_len: int = MAX_LABEL_LEN) -> str:
    """回傳消毒後的字串；None／NaN 等非字串一律先 str()。"""
    raw = "" if s is None else str(s)
    out = _BAD.sub("", raw).strip()
    if len(out) > max_len:
        out = out[:max_len]
    if out != raw.strip():
        logger.warning("sanitize_label 已改寫標籤：%r -> %r", raw, out)
    return out
