#!/usr/bin/env python3
"""no-op commit 守門：staged 的 data/*.json 若只有時間戳變了，就不要 commit。

背景（2026-09-06 實測）：每交易日約 3 次 data commit（哨兵 17:01 首觸發＋後續哨兵／備援
cron 冪等重跑），近 12 個交易日中 7 次是「內容完全相同、只有 `generated_at` 不同」的
no-op commit，每次白白推 ~4MB 重算產物（sector_ranges 2.6MB 為大宗）。
daily.yml／verify.yml 的 Commit & push 原本只查 `git diff --cached --quiet`，看不出這種差異。

規則（刻意保守，寧可多 commit 也不可漏 commit）：
  - 只看 staged 且在 data/ 底下的 .json（含 data/daily/、data/futures/）。
  - 逐檔比對 HEAD 版本（`git show HEAD:<path>`）與 index 版本（`git show :<path>`）。
  - **只忽略頂層**的 `generated_at`／`checked_at`／`last_attempt_at` 三個時間戳鍵；
    不重排、不動任何業務欄位。巢狀的時間戳（例如 status.json 的 `healthcheck.checked_at`）
    一律算真變化——verify 班的健檢結果本來就該留痕。
  - status.json 的 `status`／`expected_date`／`actual_date`／`last_success_at` 變化都是真變化：
    daily.yml 在 missing 時依賴 status.json 被推上 main（2026-09-06 F1 修法），不可被本守門吃掉。
  - 新增檔、刪除檔、改名、任一版本不是合法 JSON、git 讀不到 → 一律視為真變化。

退出碼：
  0  NOOP  ——所有 staged 的 data/*.json 去掉頂層時間戳後與 HEAD 完全相同（或根本沒 staged 檔）
  1  CHANGED ——至少一檔有真變化（stdout 逐行列出檔名）
  2  守門自己失敗（git 不可用等）——呼叫端應視同 CHANGED、照常 commit（fail-safe）

用法：`git add data/ && python src/noop_guard.py && echo "內容無變化"`
純標準函式庫、免 token、免網路。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# 只忽略「頂層」這三個鍵；名單刻意不含 last_success_at（它變＝這次真的成功產出，屬狀態變化）
TIMESTAMP_KEYS = frozenset({"generated_at", "checked_at", "last_attempt_at"})


def strip_timestamps(obj):
    """去掉頂層時間戳鍵；非 dict 原樣回傳。只動頂層，巢狀不碰。"""
    if isinstance(obj, dict):
        return {k: v for k, v in obj.items() if k not in TIMESTAMP_KEYS}
    return obj


def _git(args: list[str], cwd: Path) -> tuple[int, bytes]:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    return p.returncode, p.stdout


def staged_json_paths(repo: Path, prefix: str = "data/") -> list[tuple[str, str]]:
    """回 [(status_letter, path)]；只留 prefix 底下的 .json。status 取 --name-status 首字母。"""
    rc, out = _git(["diff", "--cached", "--name-status", "-z", "--", prefix], repo)
    if rc != 0:
        raise RuntimeError("git diff --cached 失敗")
    parts = out.decode("utf-8", "replace").split("\0")
    res: list[tuple[str, str]] = []
    i = 0
    while i < len(parts):
        st = parts[i]
        if not st:
            i += 1
            continue
        letter = st[0]
        if letter in ("R", "C"):  # 改名/複製：格式為 R100 \0 old \0 new
            old, new = parts[i + 1], parts[i + 2]
            i += 3
            res.append((letter, new))
            res.append((letter, old))
        else:
            path = parts[i + 1]
            i += 2
            res.append((letter, path))
    return [(s, p) for s, p in res if p.endswith(".json")]


def _load(repo: Path, spec: str):
    """讀 git 物件並解析 JSON；讀不到或不是 JSON 回 None（呼叫端視為真變化）。"""
    rc, out = _git(["show", spec], repo)
    if rc != 0:
        return None
    try:
        return json.loads(out.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def file_is_noop(repo: Path, status: str, path: str) -> bool:
    """單檔判定：只有 M（修改）且兩版去頂層時間戳後相等才算 no-op。"""
    if status != "M":
        return False
    head = _load(repo, f"HEAD:{path}")
    idx = _load(repo, f":{path}")
    if head is None or idx is None:
        return False
    return strip_timestamps(head) == strip_timestamps(idx)


def changed_files(repo: Path, prefix: str = "data/") -> list[str]:
    """回有真變化的檔名清單（空清單＝NOOP）。"""
    return [p for s, p in staged_json_paths(repo, prefix) if not file_is_noop(repo, s, p)]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    repo = Path(argv[0]) if argv else Path(__file__).resolve().parent.parent
    try:
        changed = changed_files(repo)
    except Exception as e:  # noqa: BLE001 —守門自己壞掉要 fail-safe，交回呼叫端照常 commit
        print(f"noop_guard 無法判定（{e}），視同有變化", file=sys.stderr)
        return 2
    if not changed:
        print("NOOP")
        return 0
    print("CHANGED")
    for p in changed:
        print(p)
    return 1


if __name__ == "__main__":
    sys.exit(main())
