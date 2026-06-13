# src/finmind.py
# FinMind API 共用 client（taiwan-flows 全 pipeline 共用）
#
# 設計：
#   - 全市場單日查詢為主（dataset + date），不逐檔迴圈
#   - 402（用量上限）/ 非 200 status 一律回 None，由呼叫端決定 retry
#   - token 由 .env 的 FINMIND_TOKEN 提供

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"
FINMIND_USER_INFO = "https://api.web.finmindtrade.com/v2/user_info"
API_SLEEP = 0.3  # 每次呼叫後 sleep，避免觸發頻率限制


def _load_token() -> str:
    """從環境變數或 repo 根目錄 .env 讀取 FINMIND_TOKEN。"""
    token = os.environ.get("FINMIND_TOKEN")
    if token:
        return token.strip()
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("FINMIND_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("找不到 FINMIND_TOKEN（環境變數或 .env 都沒有）")


TOKEN = _load_token()


def fm_get(dataset: str, retries: int = 3, backoff: float = 10.0, **params) -> Optional[pd.DataFrame]:
    """
    FinMind /data 查詢。

    回傳：
        DataFrame（有資料）、空 DataFrame（查詢成功但無列）、None（失敗）。
    參數：
        dataset：FinMind dataset 名
        retries：402 / 連線錯誤時的重試次數
        backoff：重試間隔秒數（FinMind 尚未更新資料時給它時間）
        **params：data_id、date、start_date、end_date 等
    """
    query = {"dataset": dataset, "token": TOKEN, **params}
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(FINMIND_BASE, params=query, timeout=30)
            if r.status_code == 402:
                logger.warning(f"[FinMind 402] {dataset} 用量上限/權限不足 (attempt {attempt})")
                last_err = "402"
                time.sleep(backoff)
                continue
            r.raise_for_status()
            body = r.json()
            if body.get("status") != 200:
                logger.warning(f"[FinMind] {dataset} status={body.get('status')} msg={body.get('msg','')}")
                last_err = body.get("msg", "non-200")
                time.sleep(backoff)
                continue
            data = body.get("data", [])
            time.sleep(API_SLEEP)
            return pd.DataFrame(data) if data else pd.DataFrame()
        except Exception as e:
            last_err = str(e)
            logger.warning(f"[FinMind] {dataset} 連線錯誤 (attempt {attempt}): {e}")
            time.sleep(backoff)
    logger.error(f"[FinMind] {dataset} 失敗（{retries} 次重試後）: {last_err}")
    return None


def check_quota() -> dict:
    """查詢今日 API 用量，回傳 {'used': int, 'limit': int}。"""
    try:
        r = requests.get(
            FINMIND_USER_INFO,
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=10,
        )
        r.raise_for_status()
        d = r.json()
        return {"used": d.get("user_count", -1), "limit": d.get("api_request_limit", -1)}
    except Exception as e:
        logger.warning(f"[FinMind] 用量查詢失敗: {e}")
        return {"used": -1, "limit": -1}
