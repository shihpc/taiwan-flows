#!/usr/bin/env python3
"""真交易日 no_data 不再靜默（2026-09-06）的回歸測試——免 token、免網路。

守的東西：
  1. run_daily.classify_no_data：週末→no_data、平日截止前→waiting、平日過截止→missing
     （含 Actions 延遲跨午夜、meta.calendar 已知交易日）。
  2. run_daily.main 在無資料時寫進 status.json 的新欄位契約：
     expected_date / actual_date / last_attempt_at / last_success_at（失敗保留舊值）。
  3. healthcheck.row_count_severity：列數 60% → warn、40% → critical。
  4. verify_daily.resolve_date：優先 status.json 的預期交易日，不回退到 data/daily 昨天的檔。

日期一律相對「現在」計算、不寫死（claude-harness lessons 2026-09-03：寫死日期＝時間炸彈）。
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import healthcheck  # noqa: E402
import run_daily  # noqa: E402
import verify_daily  # noqa: E402

TPE = timezone(timedelta(hours=8))
TODAY = datetime.now(TPE).date()


def _recent(pred, start: date = TODAY) -> date:
    """從 start 往回找第一個滿足 pred 的日子。"""
    d = start
    while not pred(d):
        d -= timedelta(days=1)
    return d


WEEKDAY = _recent(lambda d: d.weekday() < 5)          # 最近的平日
WEEKEND = _recent(lambda d: d.weekday() >= 5)         # 最近的週末
DEADLINE_H = run_daily.PUBLISH_DEADLINE_HOUR


def _at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=TPE)


# ---------- 1. classify_no_data ----------

@pytest.mark.parametrize("name,target,now,expect", [
    ("交易日已過截止仍缺料 → missing", WEEKDAY, _at(WEEKDAY, DEADLINE_H, 0), "missing"),
    ("交易日 21:19 cron 準點缺料 → missing", WEEKDAY, _at(WEEKDAY, 21, 19), "missing"),
    ("Actions 延遲跨午夜（隔日 01:10）→ missing", WEEKDAY, _at(WEEKDAY + timedelta(days=1), 1, 10), "missing"),
    ("交易日截止前一分鐘 → waiting", WEEKDAY, _at(WEEKDAY, DEADLINE_H - 1, 59), "waiting"),
    ("哨兵時窗 17:05 首探 → waiting", WEEKDAY, _at(WEEKDAY, 17, 5), "waiting"),
    ("週末 → no_data", WEEKEND, _at(WEEKEND, 21, 19), "no_data"),
    ("週末隔日凌晨 → no_data", WEEKEND, _at(WEEKEND + timedelta(days=1), 1, 0), "no_data"),
])
def test_classify_no_data(name, target, now, expect):
    assert run_daily.classify_no_data(target.isoformat(), now, []) == expect, name


def test_classify_calendar_known_day_skips_weekend_rule():
    """meta.calendar 已含該日＝確定是交易日，即使落在週末（補班日）也走截止判定。"""
    t = WEEKEND.isoformat()
    assert run_daily.classify_no_data(t, _at(WEEKEND, 10), [t]) == "waiting"
    assert run_daily.classify_no_data(t, _at(WEEKEND, 22), [t]) == "missing"


def test_classify_accepts_non_tpe_now():
    """now 若帶其他時區（例如 UTC），要換算成台北再比截止。"""
    now_utc = _at(WEEKDAY, DEADLINE_H - 1).astimezone(timezone.utc)   # 台北 19:00 = UTC 11:00
    assert run_daily.classify_no_data(WEEKDAY.isoformat(), now_utc, []) == "waiting"


# ---------- 2. run_daily.main 的 status.json 契約 ----------

class _Frozen:
    """把 run_daily.datetime 換成固定 now 的替身（沿用 test_trading_day 的作法）。"""
    def __init__(self, now: datetime):
        self.now_value = now

    def __enter__(self):
        now = self.now_value

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now
        self._p = mock.patch.object(run_daily, "datetime", FrozenDatetime)
        self._p.__enter__()
        return self

    def __exit__(self, *a):
        self._p.__exit__(*a)


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    """把 run_daily 的 DATA/STATUS_PATH 指到暫存目錄，種一個「昨天」的 daily 檔與舊 status。"""
    (tmp_path / "daily").mkdir()
    prev_day = WEEKDAY - timedelta(days=1)
    (tmp_path / "daily" / f"{prev_day:%Y%m%d}.json").write_text('{"rows":[]}', encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({"calendar": [prev_day.isoformat()]}), encoding="utf-8")
    old_success = "2000-01-01T00:00:00+08:00"
    (tmp_path / "status.json").write_text(json.dumps(
        {"date": prev_day.isoformat(), "status": "ok", "last_success_at": old_success}), encoding="utf-8")
    monkeypatch.setattr(run_daily, "DATA", tmp_path)
    monkeypatch.setattr(run_daily, "STATUS_PATH", tmp_path / "status.json")
    return {"dir": tmp_path, "prev_day": prev_day.isoformat(), "old_success": old_success}


def _read(tmp) -> dict:
    return json.loads((tmp["dir"] / "status.json").read_text(encoding="utf-8"))


def test_main_missing_writes_contract_and_exits_1(tmp_data, monkeypatch):
    monkeypatch.setattr(run_daily, "run_date", lambda d: False)
    with _Frozen(_at(WEEKDAY, 21, 19)):
        with pytest.raises(SystemExit) as ex:
            run_daily.main([])
    assert ex.value.code == 1
    st = _read(tmp_data)
    assert st["status"] == "missing"
    assert st["expected_date"] == WEEKDAY.isoformat()
    assert st["actual_date"] == tmp_data["prev_day"]          # 最新 daily 檔仍是昨天
    assert st["last_success_at"] == tmp_data["old_success"]   # 失敗時保留舊值
    assert st["last_attempt_at"].startswith(WEEKDAY.isoformat())


def test_main_waiting_exits_0(tmp_data, monkeypatch):
    monkeypatch.setattr(run_daily, "run_date", lambda d: False)
    with _Frozen(_at(WEEKDAY, 17, 5)):
        run_daily.main([])   # 不丟 SystemExit＝exit 0
    st = _read(tmp_data)
    assert st["status"] == "waiting"
    assert st["last_success_at"] == tmp_data["old_success"]


def test_main_weekend_no_data(tmp_data, monkeypatch):
    monkeypatch.setattr(run_daily, "run_date", lambda d: False)
    with _Frozen(_at(WEEKEND, 21, 19)):
        run_daily.main([])
    assert _read(tmp_data)["status"] == "no_data"


def test_main_ok_updates_last_success(tmp_data, monkeypatch):
    def fake_run_date(d):
        (tmp_data["dir"] / "daily" / f"{d.replace('-', '')}.json").write_text('{"rows":[]}', encoding="utf-8")
        return True
    monkeypatch.setattr(run_daily, "run_date", fake_run_date)
    monkeypatch.setattr(run_daily, "rebuild_products", lambda: None)
    with _Frozen(_at(WEEKDAY, 21, 19)):
        run_daily.main([])
    st = _read(tmp_data)
    assert st["status"] == "ok"
    assert st["expected_date"] == st["actual_date"] == WEEKDAY.isoformat()
    assert st["last_success_at"].startswith(WEEKDAY.isoformat())
    assert st["healthcheck"]["severity"] == "pending"


# ---------- 3. 列數健全性 ----------

@pytest.mark.parametrize("n,expect", [
    (100, "ok"), (80, "ok"), (79, "warn"), (60, "warn"), (50, "warn"), (49, "critical"), (40, "critical"),
])
def test_row_count_severity(n, expect):
    sev, med = healthcheck.row_count_severity(n, [100] * 20)
    assert (sev, med) == (expect, 100.0)


def test_row_count_severity_median_robust_to_outlier():
    hist = [2650] * 19 + [10]          # 一個壞檔不該拉低中位數
    assert healthcheck.row_count_severity(2600, hist)[0] == "ok"
    assert healthcheck.row_count_severity(1500, hist)[0] == "warn"
    assert healthcheck.row_count_severity(1300, hist)[0] == "critical"


def test_row_count_severity_no_history_is_ok():
    assert healthcheck.row_count_severity(5, []) == ("ok", None)


def test_worst_severity():
    assert healthcheck.worst_severity("ok", "warn") == "warn"
    assert healthcheck.worst_severity("critical", "ok") == "critical"
    assert healthcheck.worst_severity("ok", "ok") == "ok"


# ---------- 4. verify 日期選取不回退到昨天 ----------

def test_verify_resolve_date_prefers_expected_date(monkeypatch):
    today, yday = WEEKDAY.isoformat(), (WEEKDAY - timedelta(days=1)).isoformat()
    monkeypatch.setattr(healthcheck, "latest_daily_date", lambda: yday)   # 當天沒產檔，末檔是昨天
    st = {"date": today, "status": "missing", "expected_date": today}
    assert verify_daily.resolve_date(None, st) == today
    assert verify_daily.resolve_date(None, {"date": today, "status": "missing"}) == today  # 舊格式無 expected_date
    assert verify_daily.resolve_date("2020-01-02", st) == "2020-01-02"                     # --date 最優先
    assert verify_daily.resolve_date(None, {}) == yday                                     # 完全沒 status 才退回末檔
