#!/usr/bin/env python3
"""src/sanitize.py 的回歸測試——免 token、免網路。

守的東西：
  1. 含 <img onerror> 等標籤／屬性邊界字元的名稱被清乾淨（< > " ' 全部消失）。
  2. 正常中文名（全形括號、「－」、-KY、S&P）原樣不變——`&` 是真實資料就有的字元，不可拿掉。
  3. 超過 MAX_LABEL_LEN 截斷；None／非字串不炸。
  4. 現行 data/meta.json 與 industry_chain.json 的所有標籤跑過消毒後零變動
     （保證 budget.py 那一道再消毒對 parity 是 no-op）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sanitize import MAX_LABEL_LEN, sanitize_label  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_strips_html_injection():
    bad = '<img src=x onerror="window.__xss=1">'
    out = sanitize_label(bad)
    assert not any(ch in out for ch in "<>\"'")
    assert out == "img src=x onerror=window.__xss=1"
    assert sanitize_label("台積電<script>alert(1)</script>") == "台積電scriptalert(1)/script"
    assert sanitize_label("a' onmouseover='x") == "a onmouseover=x"
    assert sanitize_label("x\x00y\x1f\x7fz") == "xyz"


def test_normal_names_unchanged():
    for name in ["台積電", "亞獅康-KY", "元大S&P500", "FT1-3年美公債", "凱基新興債1-5",
                 "統一（台灣）", "台灣高鐵－特別股", "MR Headset & SG",
                 "無線通訊設備(如行動電話、衛星定位系統、衛星通訊設備、微波通訊設備、數位機上盒)"]:
        assert sanitize_label(name) == name


def test_truncate_and_non_str():
    long = "甲" * (MAX_LABEL_LEN + 5)
    assert sanitize_label(long) == "甲" * MAX_LABEL_LEN
    assert sanitize_label(long, max_len=3) == "甲甲甲"
    assert sanitize_label(None) == ""
    assert sanitize_label(1234) == "1234"
    assert sanitize_label("  台積電  ") == "台積電"


def test_current_data_is_noop():
    meta = json.loads((ROOT / "data/meta.json").read_text(encoding="utf-8"))
    for v in meta["stocks"].values():
        assert sanitize_label(v["name"]) == v["name"]
        assert sanitize_label(v["industry"]) == str(v["industry"])
    chain = json.loads((ROOT / "data/industry_chain.json").read_text(encoding="utf-8"))
    for e in chain["map"].values():
        for lbl in e["i"] + e["s"] + [x for p in e["p"] for x in p]:
            assert sanitize_label(lbl) == lbl
