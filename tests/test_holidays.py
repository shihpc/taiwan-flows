#!/usr/bin/env python3
"""國定假日行事曆接入（2026-09-28，批次一）的回歸測試——免 token、免網路。

規格：taiwan-flow-live-v2 docs/holiday-calendar.md §1／§2／§3（taiwan-flows 列）。
守的東西：
  1. twse_holidays.load()/parse()：合法檔解析；404／逾時／壞 JSON／形狀不合 → None（fail-open，不拋）。
  2. classify_no_data：2026-09-25（週五中秋）／09-28（週一）→ no_data；09-29（假日後首日）照舊
     waiting／missing；行事曆讀不到或年度未涵蓋 → 退回只排週末（假日照舊 missing）；
     meta.calendar 已知交易日優先於行事曆。
  3. run_daily.main：假日無資料 → status=no_data、note 寫明國定假日、exit 0；fail-open 時照舊 missing exit 1。
  4. verify_daily.main：status 為 missing／waiting 但目標日是假日 → 不重跑 pipeline、不判 critical、
     exit 0，並把 status 更正為 no_data；行事曆讀不到時 missing 照舊走補回。

這裡的日期是**寫死的**（2026-09-25 等），但不是時間炸彈：假日集合由測試自己給（不依賴網路與
今天日期），now 也一律明確傳入／凍結。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import run_daily  # noqa: E402
import twse_holidays  # noqa: E402
import verify_daily  # noqa: E402

TPE = timezone(timedelta(hours=8))
HOL = twse_holidays.TwseHolidays(
    years=frozenset({2026}),
    closed=frozenset({"2026-01-01", "2026-09-25", "2026-09-26", "2026-09-28"}),  # 含週末日期（契約：照收）
    names={"2026-09-25": "中秋節", "2026-09-28": "教師節"},
)
DOC = {"schema": 1, "source": "x", "fetched_at": "2026-09-28T08:00:00+08:00", "years": [2026],
       "closed": sorted(HOL.closed), "names": HOL.names, "raw_n": 27}


def _at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=TPE)


# ---------- 1. 載入與 fail-open ----------

def test_load_valid_doc():
    cal = twse_holidays.load(fetch=lambda url, t: json.dumps(DOC).encode("utf-8"))
    assert cal is not None and cal.is_holiday("2026-09-25") and cal.name("2026-09-28") == "教師節"
    assert not cal.is_holiday("2026-09-29")


def test_load_passes_timeout():
    seen = {}

    def f(url, timeout):
        seen["t"] = timeout
        return json.dumps(DOC)
    twse_holidays.load(fetch=f)
    assert seen["t"] == twse_holidays.FETCH_TIMEOUT_SEC and 0 < seen["t"] <= 30


@pytest.mark.parametrize("name,fetch", [
    ("404", lambda u, t: (_ for _ in ()).throw(RuntimeError("HTTP 404"))),
    ("逾時", lambda u, t: (_ for _ in ()).throw(TimeoutError("timed out"))),
    ("非 JSON", lambda u, t: b"<html>404</html>"),
    ("非 UTF-8", lambda u, t: b"\xff\xfe\x00"),
    ("schema 不對", lambda u, t: json.dumps({**DOC, "schema": 2})),
    ("schema 為 true", lambda u, t: json.dumps({**DOC, "schema": True})),
    ("schema 為字串", lambda u, t: json.dumps({**DOC, "schema": "1"})),
    ("years 缺", lambda u, t: json.dumps({k: v for k, v in DOC.items() if k != "years"})),
    ("years 空", lambda u, t: json.dumps({**DOC, "years": []})),
    ("closed 非日期", lambda u, t: json.dumps({**DOC, "closed": ["1150925"]})),
    ("頂層是陣列", lambda u, t: json.dumps([1, 2])),
])
def test_load_fail_open(name, fetch):
    assert twse_holidays.load(fetch=fetch) is None, name


def test_default_fetch_failure_does_not_raise(monkeypatch):
    """預設抓取（conftest 已換成立即失敗）也不得拋例外。"""
    assert twse_holidays.load() is None


def test_year_not_covered_is_unknown():
    assert not HOL.is_holiday("2027-01-01")          # 2027 不在 years → 未知 → 不當假日
    cal = twse_holidays.TwseHolidays(frozenset({2027}), frozenset({"2026-09-25"}))
    assert not cal.is_holiday("2026-09-25")          # closed 有、但年度未涵蓋 → 不採信
    assert not twse_holidays.is_holiday("2026-09-25", None)


# ---------- 2. classify_no_data ----------

@pytest.mark.parametrize("name,target,now,hol,expect", [
    ("09-25 週五中秋 21:19", "2026-09-25", _at(2026, 9, 25, 21, 19), HOL, "no_data"),
    ("09-25 延遲跨午夜 09-26 01:10", "2026-09-25", _at(2026, 9, 26, 1, 10), HOL, "no_data"),
    ("09-25 截止前 17:05 也不等", "2026-09-25", _at(2026, 9, 25, 17, 5), HOL, "no_data"),
    ("09-28 週一假日 21:19", "2026-09-28", _at(2026, 9, 28, 21, 19), HOL, "no_data"),
    ("09-28 延遲跨午夜 09-29 01:10", "2026-09-28", _at(2026, 9, 29, 1, 10), HOL, "no_data"),
    ("09-29 假日後首日 截止前 → waiting", "2026-09-29", _at(2026, 9, 29, 17, 5), HOL, "waiting"),
    ("09-29 假日後首日 過截止缺料 → missing", "2026-09-29", _at(2026, 9, 29, 21, 19), HOL, "missing"),
    ("週末照舊 no_data", "2026-09-27", _at(2026, 9, 27, 21, 19), HOL, "no_data"),
    ("行事曆讀不到 → 09-25 退回只排週末 missing", "2026-09-25", _at(2026, 9, 25, 21, 19), None, "missing"),
    ("行事曆讀不到 → 週末仍 no_data", "2026-09-27", _at(2026, 9, 27, 21, 19), None, "no_data"),
    ("年度未涵蓋 → 2027-01-01 退回只排週末 missing", "2027-01-01", _at(2027, 1, 1, 21, 19), HOL, "missing"),
])
def test_classify_with_holidays(name, target, now, hol, expect):
    assert run_daily.classify_no_data(target, now, [], hol) == expect, name


def test_calendar_known_day_beats_holiday_list():
    """meta.calendar 已成功抓過＝確定交易日；行事曆若誤列也不採信（寧可告警不可吞掉）。"""
    assert run_daily.classify_no_data("2026-09-25", _at(2026, 9, 25, 21, 19),
                                      ["2026-09-25"], HOL) == "missing"


def test_default_arg_is_weekend_only():
    """不傳 holidays＝舊行為（既有測試的呼叫形狀）。"""
    assert run_daily.classify_no_data("2026-09-25", _at(2026, 9, 25, 21, 19), []) == "missing"


# ---------- 3. run_daily.main ----------

class _Frozen:
    def __init__(self, now):
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
    (tmp_path / "daily").mkdir()
    (tmp_path / "daily" / "20260924.json").write_text('{"rows":[]}', encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({"calendar": ["2026-09-24"]}), encoding="utf-8")
    (tmp_path / "status.json").write_text(json.dumps(
        {"date": "2026-09-24", "status": "ok", "last_success_at": "2000-01-01T00:00:00+08:00"}),
        encoding="utf-8")
    monkeypatch.setattr(run_daily, "DATA", tmp_path)
    monkeypatch.setattr(run_daily, "STATUS_PATH", tmp_path / "status.json")
    monkeypatch.setattr(run_daily, "run_date", lambda d: False)
    return tmp_path


def _st(tmp):
    return json.loads((tmp / "status.json").read_text(encoding="utf-8"))


def _with_cal(monkeypatch, cal):
    monkeypatch.setattr(twse_holidays, "load", lambda *a, **k: cal)


@pytest.mark.parametrize("day,now,nm", [
    ("2026-09-25", _at(2026, 9, 25, 21, 19), "中秋節"),
    ("2026-09-28", _at(2026, 9, 29, 1, 10), "教師節"),   # 跨午夜回推一天到假日
])
def test_main_holiday_no_data_exit0(tmp_data, monkeypatch, day, now, nm):
    _with_cal(monkeypatch, HOL)
    with _Frozen(now):
        run_daily.main([])            # 不丟 SystemExit＝exit 0
    st = _st(tmp_data)
    assert st["date"] == st["expected_date"] == day
    assert st["status"] == "no_data" and "國定假日" in st["note"] and nm in st["note"]
    assert st["actual_date"] == "2026-09-24"
    assert st["last_success_at"] == "2000-01-01T00:00:00+08:00"
    # 形狀不變：no_data 路徑與週末相同的鍵集合
    assert set(st) == {"date", "status", "note", "expected_date", "actual_date", "sources",
                       "checked_at", "last_attempt_at", "last_success_at"}


def test_main_holiday_fail_open_still_missing(tmp_data, monkeypatch):
    """行事曆讀不到（conftest 的離線抓取）→ 09-25 照舊 missing、exit 1，不拋其他例外。"""
    with _Frozen(_at(2026, 9, 25, 21, 19)):
        with pytest.raises(SystemExit) as ex:
            run_daily.main([])
    assert ex.value.code == 1
    st = _st(tmp_data)
    assert st["status"] == "missing" and "行事曆未涵蓋" in st["note"]


def test_main_day_after_holiday_missing(tmp_data, monkeypatch):
    _with_cal(monkeypatch, HOL)
    with _Frozen(_at(2026, 9, 29, 21, 19)):
        with pytest.raises(SystemExit) as ex:
            run_daily.main([])
    assert ex.value.code == 1
    st = _st(tmp_data)
    assert st["status"] == "missing" and st["expected_date"] == "2026-09-29"
    assert "行事曆未涵蓋" not in st["note"]


# ---------- 4. verify_daily ----------

def _verify(tmp_data, monkeypatch, status, day, now, cal):
    (tmp_data / "status.json").write_text(json.dumps(
        {"date": day, "status": status, "note": "x", "expected_date": day,
         "last_success_at": "2000-01-01T00:00:00+08:00"}), encoding="utf-8")
    _with_cal(monkeypatch, cal)
    calls = []
    import pipeline
    monkeypatch.setattr(pipeline, "run_date", lambda d: calls.append(d) or False)
    monkeypatch.setattr(verify_daily.healthcheck, "check",
                        lambda d: (_ for _ in ()).throw(AssertionError("不應健檢")))
    with _Frozen(now):
        with pytest.raises(SystemExit) as ex:
            verify_daily.main([])
    return ex.value.code, calls, _st(tmp_data)


@pytest.mark.parametrize("status", ["missing", "waiting", "error"])
def test_verify_holiday_no_recover(tmp_data, monkeypatch, status):
    rc, calls, st = _verify(tmp_data, monkeypatch, status, "2026-09-25", _at(2026, 9, 25, 23, 40), HOL)
    assert rc == verify_daily.EXIT_OK
    assert calls == []                                   # 沒重跑 pipeline
    assert st["status"] == "no_data" and "國定假日" in st["note"]
    assert "healthcheck" not in st                       # 沒判 critical
    assert st["expected_date"] == "2026-09-25"


def test_verify_no_data_holiday_untouched(tmp_data, monkeypatch):
    rc, calls, st = _verify(tmp_data, monkeypatch, "no_data", "2026-09-28", _at(2026, 9, 28, 23, 40), HOL)
    assert rc == verify_daily.EXIT_OK and calls == [] and st["note"] == "x"


def test_verify_holiday_fail_open_still_recovers(tmp_data, monkeypatch):
    """行事曆讀不到 → 09-25 missing 照舊走補回（重跑 pipeline、補不回→critical、exit 2）。"""
    rc, calls, st = _verify(tmp_data, monkeypatch, "missing", "2026-09-25", _at(2026, 9, 25, 23, 40), None)
    assert calls == ["2026-09-25"] and rc == verify_daily.EXIT_UNUSABLE
    assert st["status"] == "missing" and st["healthcheck"]["severity"] == "critical"


def test_verify_day_after_holiday_missing_recovers(tmp_data, monkeypatch):
    rc, calls, st = _verify(tmp_data, monkeypatch, "missing", "2026-09-29", _at(2026, 9, 29, 23, 40), HOL)
    assert calls == ["2026-09-29"] and rc == verify_daily.EXIT_UNUSABLE
    assert st["status"] == "missing"


def test_verify_missing_not_downgraded_to_waiting(tmp_data, monkeypatch):
    """排程已判 missing，verify 時鐘早於截止（手動 dispatch）也不改判 waiting 放過。"""
    rc, calls, st = _verify(tmp_data, monkeypatch, "missing", "2026-09-29", _at(2026, 9, 29, 18, 0), HOL)
    assert calls == ["2026-09-29"]
