"""pytest 共用設定（2026-09-28）。

run_daily.main／verify_daily.main 在無資料時會呼叫 twse_holidays.load() 抓家族國定假日行事曆
（raw.githubusercontent.com）。測試一律離線：把預設抓取換成「立即失敗」，走 fail-open＝只排週末，
與行事曆上線前的行為相同，既有測試語意不變。需要行事曆的測試自己注入 TwseHolidays／fetch。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import twse_holidays  # noqa: E402


@pytest.fixture(autouse=True)
def _offline_holidays(monkeypatch):
    def _no_network(url, timeout):
        raise RuntimeError("tests are offline")
    monkeypatch.setattr(twse_holidays, "_default_fetch", _no_network)


@pytest.fixture(autouse=True)
def _offline_sentiment(monkeypatch):
    """市場情緒（2026-09-29）：run_daily ok 路徑會呼叫 sentiment.update()。預設抓取（FinMind／
    期交所 OpenAPI）一律換成立即失敗，確保測試離線；需要資料的測試自己注入 fetch。"""
    import sentiment

    def _no_fm(dataset, **params):
        raise RuntimeError("tests are offline")

    def _no_taifex():
        raise RuntimeError("tests are offline")
    monkeypatch.setattr(sentiment, "_default_fetch", _no_fm)
    monkeypatch.setattr(sentiment, "_default_taifex", _no_taifex)
