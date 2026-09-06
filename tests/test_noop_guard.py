#!/usr/bin/env python3
"""src/noop_guard.py 的回歸測試——免 token、免網路（只用本機臨時 git repo）。

守的東西：
  1. staged 的 data/*.json 只有頂層 generated_at／checked_at／last_attempt_at 變 → NOOP（exit 0）。
  2. status.json 的 status 改 missing（或 last_success_at 變）→ 真變化（daily.yml F1 修法依賴它被推上去）。
  3. daily/ 逐檔列數變 → 真變化。
  4. 新檔 / 刪檔 / 壞 JSON → 真變化（保守：寧可多 commit）。
  5. 巢狀時間戳（healthcheck.checked_at）不在忽略名單 → 真變化。
  6. 沒有 staged 檔 → NOOP；data/ 以外的檔不納入判定。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import noop_guard  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _write(repo: Path, rel: str, obj) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


BASE = {
    "data/latest.json": {"date": "2026-09-04", "generated_at": "2026-09-04T21:01:00+08:00",
                         "window": "1d", "pages": {"foreign": {"buy": [1, 2, 3]}}},
    "data/sector_ranges.json": {"date": "2026-09-04", "generated_at": "2026-09-04T21:01:05+08:00",
                                "windows": {"r5": {"stocks": [["2330", 1.5]]}}},
    "data/status.json": {"date": "2026-09-04", "status": "ok", "note": "",
                         "expected_date": "2026-09-04", "actual_date": "2026-09-04",
                         "checked_at": "2026-09-04T21:01:06+08:00",
                         "last_attempt_at": "2026-09-04T21:01:06+08:00",
                         "last_success_at": "2026-09-04T21:01:06+08:00",
                         "healthcheck": {"date": "2026-09-04", "severity": "pending",
                                         "checked_at": "2026-09-04T21:01:06+08:00"}},
    "data/daily/20260904.json": {"date": "2026-09-04", "cols": ["code", "close"],
                                 "rows": [["2330", 1000], ["2317", 200]]},
    "data/futures/20260904.json": {"date": "2026-09-04", "cols": ["id", "oi"], "rows": [["fx", -1]]},
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    for rel, obj in BASE.items():
        _write(r, rel, obj)
    (r / "index.html").write_text("<p>x</p>", encoding="utf-8")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "base")
    return r


def _bump_ts(obj: dict, suffix: str = "T22:30:00+08:00") -> dict:
    o = dict(obj)
    for k in ("generated_at", "checked_at", "last_attempt_at"):
        if k in o:
            o[k] = "2026-09-04" + suffix
    return o


def _run_cli(repo: Path) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(Path(noop_guard.__file__)), str(repo)],
                       capture_output=True, text=True)
    return p.returncode, p.stdout


def test_only_timestamps_changed_is_noop(repo: Path):
    for rel in ("data/latest.json", "data/sector_ranges.json"):
        _write(repo, rel, _bump_ts(BASE[rel]))
    st = _bump_ts(BASE["data/status.json"])
    _write(repo, "data/status.json", st)
    _git(repo, "add", "data/")
    assert noop_guard.changed_files(repo) == []
    rc, out = _run_cli(repo)
    assert rc == 0 and out.strip() == "NOOP"


def test_nothing_staged_is_noop(repo: Path):
    assert noop_guard.changed_files(repo) == []
    assert _run_cli(repo)[0] == 0


def test_status_missing_is_real_change(repo: Path):
    st = _bump_ts(BASE["data/status.json"])
    st["status"] = "missing"
    st["expected_date"] = "2026-09-05"
    _write(repo, "data/status.json", st)
    _git(repo, "add", "data/")
    assert noop_guard.changed_files(repo) == ["data/status.json"]
    rc, out = _run_cli(repo)
    assert rc == 1 and out.splitlines()[0] == "CHANGED" and "data/status.json" in out


def test_last_success_at_is_real_change(repo: Path):
    st = _bump_ts(BASE["data/status.json"])
    st["last_success_at"] = "2026-09-04T22:30:00+08:00"
    _write(repo, "data/status.json", st)
    _git(repo, "add", "data/")
    assert noop_guard.changed_files(repo) == ["data/status.json"]


def test_nested_timestamp_is_real_change(repo: Path):
    st = _bump_ts(BASE["data/status.json"])
    st["healthcheck"] = dict(st["healthcheck"], checked_at="2026-09-04T23:40:00+08:00")
    _write(repo, "data/status.json", st)
    _git(repo, "add", "data/")
    assert noop_guard.changed_files(repo) == ["data/status.json"]


def test_daily_row_count_change_is_real_change(repo: Path):
    d = dict(BASE["data/daily/20260904.json"])
    d["rows"] = d["rows"] + [["2454", 1500]]
    _write(repo, "data/daily/20260904.json", d)
    _write(repo, "data/latest.json", _bump_ts(BASE["data/latest.json"]))  # 同批的純時戳檔不該被列出
    _git(repo, "add", "data/")
    assert noop_guard.changed_files(repo) == ["data/daily/20260904.json"]
    assert _run_cli(repo)[0] == 1


def test_new_file_is_real_change(repo: Path):
    _write(repo, "data/daily/20260905.json", {"date": "2026-09-05", "cols": ["code"], "rows": [["2330"]]})
    _git(repo, "add", "data/")
    assert noop_guard.changed_files(repo) == ["data/daily/20260905.json"]


def test_deleted_file_is_real_change(repo: Path):
    (repo / "data/futures/20260904.json").unlink()
    _git(repo, "add", "data/")
    assert noop_guard.changed_files(repo) == ["data/futures/20260904.json"]


def test_invalid_json_is_real_change(repo: Path):
    (repo / "data/latest.json").write_text("{not json", encoding="utf-8")
    _git(repo, "add", "data/")
    assert noop_guard.changed_files(repo) == ["data/latest.json"]


def test_non_data_files_ignored(repo: Path):
    (repo / "index.html").write_text("<p>y</p>", encoding="utf-8")
    _write(repo, "data/latest.json", _bump_ts(BASE["data/latest.json"]))
    _git(repo, "add", ".")
    # index.html 不在 data/ 之下，本守門不判它（工作流在 git add data/ 之後呼叫，本來就不會 stage 它）
    assert noop_guard.changed_files(repo) == []


def test_strip_timestamps_only_top_level():
    obj = {"generated_at": 1, "checked_at": 2, "last_attempt_at": 3, "last_success_at": 4,
           "nested": {"generated_at": 5}}
    out = noop_guard.strip_timestamps(obj)
    assert out == {"last_success_at": 4, "nested": {"generated_at": 5}}
    assert noop_guard.strip_timestamps([1, 2]) == [1, 2]
