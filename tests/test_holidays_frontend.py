# tests/test_holidays_frontend.py —— 代跑 tests/test_holidays_frontend.mjs（前端休市行事曆純函式，
# 2026-09-29 批次二，規格 taiwan-flow-live-v2/docs/holiday-calendar.md §5b）。需要 node；免網路、免 token。
import os
import shutil
import subprocess

import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def test_holidays_frontend_pure_functions():
    node = shutil.which("node")
    if not node:
        pytest.fail("找不到 node：本測試需要 node 執行 index.html 抽出的前端函式")
    out = subprocess.run([node, os.path.join(ROOT, "tests", "test_holidays_frontend.mjs")],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
