# src/twse_holidays.py
# 家族共用國定假日行事曆的**消費端**（2026-09-28，批次一）。
#
# 規格正本：taiwan-flow-live-v2 `docs/holiday-calendar.md`（§1 資料契約、§2 消費端共同規則）。
# 資料唯一來源＝taiwan-flow-live-v2 的 `data/twse_holidays.json`（由該 repo holidays.yml 自 TWSE
# OpenAPI 產出），本檔只負責「讀進來＋判斷某日是否為國定休市日」，不自己打 TWSE。
#
# 核心約束（§2）：**fail-open**——讀不到（404／逾時／網路例外）、壞檔（非 JSON／形狀不對）、
#   或目標年度不在 `years` 裡，一律退回「只排週末」的舊行為，絕不拋例外、絕不因行事曆
#   掛掉而把真交易日判成休市。
#   - load() 失敗回 None（呼叫端把 None 當「沒有行事曆」）。
#   - TwseHolidays.is_holiday() 只在「該年度有涵蓋且日期在 closed 裡」才回 True；
#     年度未涵蓋＝該年未知，**不得**當成「該年沒有假日」以外的任何結論（回 False＝只排週末）。
#   - 週末不在本模組判斷（closed 照收週末日期，消費端自己取聯集）。
#
# 抓取可注入（fetch 參數），離線測試不打網路。

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger("twse_holidays")

HOLIDAYS_URL = ("https://raw.githubusercontent.com/shihpc/taiwan-flow-live-v2/"
                "main/data/twse_holidays.json")
FETCH_TIMEOUT_SEC = 10
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

Fetch = Callable[[str, float], "str | bytes"]


@dataclass(frozen=True)
class TwseHolidays:
    years: frozenset[int]
    closed: frozenset[str]
    names: dict[str, str] = field(default_factory=dict)

    def covers(self, day: str) -> bool:
        """該日所屬年度是否在行事曆涵蓋範圍內。"""
        try:
            return int(day[:4]) in self.years
        except (TypeError, ValueError):
            return False

    def is_holiday(self, day: str) -> bool:
        """day（YYYY-MM-DD）是否為行事曆上的休市日。年度未涵蓋一律 False（fail-open）。"""
        return self.covers(day) and day in self.closed

    def name(self, day: str) -> str:
        return self.names.get(day, "") if self.is_holiday(day) else ""


def _default_fetch(url: str, timeout: float) -> bytes:
    import requests  # 延後 import：純函式測試不需要它
    r = requests.get(url, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.content


def parse(doc: object) -> TwseHolidays | None:
    """驗 §1 契約的形狀；任何一處不合就回 None（呼叫端 fail-open）。"""
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        return None
    years, closed, names = doc.get("years"), doc.get("closed"), doc.get("names") or {}
    if not isinstance(years, list) or not years or \
            not all(isinstance(y, int) and not isinstance(y, bool) for y in years):
        return None
    if not isinstance(closed, list) or not all(isinstance(d, str) and _DATE_RE.match(d) for d in closed):
        return None
    if not isinstance(names, dict):
        names = {}
    return TwseHolidays(frozenset(years), frozenset(closed),
                        {str(k): str(v) for k, v in names.items()})


def load(fetch: Fetch | None = None, url: str = HOLIDAYS_URL,
         timeout: float = FETCH_TIMEOUT_SEC) -> TwseHolidays | None:
    """抓並解析行事曆；失敗（含任何例外）回 None，只記 warning、不拋。"""
    try:
        raw = (fetch or _default_fetch)(url, timeout)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        cal = parse(json.loads(raw))
    except Exception as e:  # noqa: BLE001 — fail-open：任何失敗都退回只排週末
        logger.warning(f"國定假日行事曆讀不到（{type(e).__name__}: {e}），退回只排週末")
        return None
    if cal is None:
        logger.warning("國定假日行事曆形狀不合契約（schema／years／closed），退回只排週末")
    return cal


def is_holiday(day: str, cal: TwseHolidays | None) -> bool:
    """cal 為 None（沒讀到行事曆）→ False＝只排週末。"""
    return bool(cal) and cal.is_holiday(day)
