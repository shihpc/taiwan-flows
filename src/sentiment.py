# src/sentiment.py
# 市場情緒指標（2026-09-28，驗收條件正本 docs/sentiment-tab.md）→ data/sentiment.json
#
# 純描述性顯示用（鐵律 8）：只算數、不下判斷、不進任何排序或訊號。口徑全部照
# docs/sentiment-tab.md §0（2026-09-28 Hetzner 以 Sponsor token 實測、與期交所官方逐位相同），
# **不要自行改口徑**：
#   VIX          TaiwanOptionVix（date/time/vix，分鐘級）→ 當日**最後一筆**
#   Put/Call OI  TaiwanOptionDaily data_id=TXO → Σput OI ÷ Σcall OI，只取 trading_session=="position"
#   Put/Call Vol 同上 → Σput volume ÷ Σcall volume，position＋after_market 兩時段合計
#   小台散戶     TaiwanFuturesInstitutionalInvestors data_id=MTX 三大法人多／空未平倉加總
#                → 散戶淨部位＝法人空 − 法人多；散戶多空比＝散戶淨部位 ÷ 全市場未平倉
#                （全市場＝TaiwanFuturesDaily data_id=MTX、position 時段的 open_interest 加總）
#   **未定口徑**：全市場未平倉含不含週契約（contract_date 形如 202609W5）。主值 mtx_oi＝全部契約，
#   另存 mtx_oi_monthly_only（排除 contract_date 含 "W" 的週契約）供首跑比對；定案後改
#   MTX_OI_MAIN 常數即可（"all" | "monthly"）。
#   **未平倉排除當日到期契約**（2026-09-30，docs/sentiment-tab.md §0b）：put_oi／call_oi／pc_oi 與
#   mtx_oi／mtx_oi_monthly_only／retail_ratio 不計「到期日＝資料日」的契約（到期結算後已不存在，官方
#   未平倉不含它；run 36678263476 實證 09-29 扣掉 202609F4 後與期交所逐口相同）。成交量與法人多空不變。
#   到期日由 contract_date 推算（contract_expiry），非交易日順延；行事曆每班讀一次（twse_holidays.load），
#   讀不到＝fail-open 只排週末（休市順延的情形會算錯，log 註明）。每列帶 cv＝SENTIMENT_CALC_VER，
#   cv 缺或較舊的既有列由 plan_dates 當缺漏由舊到新重算。
#
# 失敗隔離（M2）：本模組由 run_daily 在既有產出完成**之後**呼叫，例外由呼叫端捕捉，
# 不影響既有產出與 exit code。例外訊息一律過 finmind.mask_secret（M3）。
#
# 用法：
#   python src/sentiment.py                  # 依 meta.calendar 補洞（每班最多 SENTIMENT_MAX_BACKFILL 天）
#   python src/sentiment.py --date 2026-09-24  # 只算（並覆寫）這一天
#   python src/sentiment.py --backfill 60    # 本次上限改 60 天
#   python src/sentiment.py --dry-run        # 算完只印、不寫檔

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from budget import jround  # noqa: E402
from finmind import mask_secret  # noqa: E402
import twse_holidays  # noqa: E402

logger = logging.getLogger("sentiment")

TPE = timezone(timedelta(hours=8))
DATA = Path(__file__).resolve().parent.parent / "data"
SENTIMENT_PATH = DATA / "sentiment.json"

SENTIMENT_START = "2026-03-02"      # TaiwanOptionVix 歷史最早日（2024-06 查詢 0 筆＝資料集本身從此開始）
SENTIMENT_MAX_BACKFILL = 20         # 每班最多算幾個交易日（每天 4 個請求，避免單班請求爆量）
SENTIMENT_MAX_CONSEC_FAIL = 2      # 連續幾天請求失敗就放棄本班（API 整個掛掉時別把 20 天 × 重試全跑完）
SENTIMENT_BUDGET_SEC = 300          # 每班牆鐘總預算（秒）：daily.yml timeout 55 分、重試迴圈已吃掉大半，
                                    # 情緒指標不可擠壓既有產出的 commit；達到即停、已算的照寫、剩餘留待下班。
                                    # 只在天與天之間檢查。單日最壞＝前 3 資料集各「2 敗 (30+10)s＋第 3 次 30s 內成功」≈110s、
                                    # 第 4 資料集 3 敗 3×(30+10)=120s → ≈451s；最壞單班≈300＋451＋期交所 15s≈12.8 分；
                                    # daily.yml 最壞總長≈47.8 分（600 秒時≈52.8 分逼近 55 分，故降為 300）。不含 checkout／pip／push；
                                    # requests timeout=30 同時是 connect 與單次 read 逾時，極慢串流理論上無上限（推估、未實測）
SENTIMENT_REFRESH_DAYS = 3          # 最近 N 個交易日若有 null 欄位，下一班重算（資料晚到）
MTX_OI_MAIN = "all"                 # 全市場未平倉主值口徑："all"（全部契約）| "monthly"（僅月契約）——首跑比對後定案
TAIFEX_PC_URL = "https://openapi.taifex.com.tw/v1/PutCallRatio"   # 只作交叉核對，不是主資料源
SCHEMA = 1
SENTIMENT_CALC_VER = 2              # 每列 cv：計算版本。1（舊列無此鍵）＝未排除當日到期契約；2＝§0b 規則。
                                    # plan_dates 把 cv < 本值的既有列當缺漏重算（仍受每班上限與預算約束）

YMD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 判斷「該日資料還沒到」要看的欄位：任一為 null 且在最近 SENTIMENT_REFRESH_DAYS 內 → 重算
KEY_FIELDS = ("vix", "pc_oi", "pc_vol", "retail_net", "mtx_oi")


_LOAD = object()                    # update(holidays=) 的「未傳」哨兵：未傳＝自己讀行事曆；傳 None＝明確 fail-open


class SentimentFetchError(RuntimeError):
    """資料集請求失敗（fm_get 回 None）——與「查詢成功但 0 筆」不同，後者只讓該欄 null。"""


# ════════════════════════════════════════════════════════════════
# 純函式（免 token、免網路；rows＝FinMind data 陣列，list[dict]）
# ════════════════════════════════════════════════════════════════

def _num(v) -> float:
    """數值欄轉 float；None／空字串／NaN／非數字一律 0（FinMind 缺值偶爾是空字串）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f != f else f  # NaN


def _ratio_pct(a: float, b: float):
    """a ÷ b，存百分比兩位小數（budget.jround＝JS Math.round 語意）；b 為 0 → None。"""
    return jround(a / b * 100, 2) if b else None


def vix_close(rows: Iterable[dict]) -> dict:
    """當日最後一筆 VIX。以 (date, time) 字典序取最大者，不假設輸入已排序。

    回傳 {vix, time, n}；無有效列 → vix/time 為 None。
    """
    best = None
    n = 0
    for r in rows or []:
        try:
            v = float(r.get("vix"))
        except (TypeError, ValueError):
            continue
        if v != v:
            continue
        n += 1
        k = (str(r.get("date") or ""), str(r.get("time") or ""))
        if best is None or k > best[0]:
            best = (k, v)
    if best is None:
        return {"vix": None, "time": None, "n": 0}
    return {"vix": jround(best[1], 2), "time": best[0][1] or None, "n": n}


def _cp(v) -> str | None:
    """call_put 欄正規化成 'call'／'put'（FinMind 為英文小寫；保險起見也吃大小寫與中文）。"""
    s = str(v or "").strip().lower()
    if s in ("call", "c", "買權"):
        return "call"
    if s in ("put", "p", "賣權"):
        return "put"
    return None


_CD_RE = re.compile(r"^(\d{4})(\d{2})(?:([WF])([1-5]))?$")


def _trading(day: date, holidays) -> bool:
    return day.weekday() < 5 and not twse_holidays.is_holiday(day.isoformat(), holidays)


def contract_expiry(contract_date, holidays=None) -> str | None:
    """contract_date → 到期日（YYYY-MM-DD）；無法解析回 None（呼叫端不排除）。

    YYYYMM（6 碼）＝該月第 3 個週三；YYYYMMWn＝第 n 個週三；YYYYMMFn＝第 n 個週五。
    推得之日非交易日（週末，或 holidays＝twse_holidays.TwseHolidays 所列休市日）→ 順延到下一個交易日。
    holidays 為 None＝行事曆未載入，只排週末（fail-open）。該月沒有第 n 個週三／週五 → None。
    價差（202610/202611）等其他形狀 → None。
    """
    m = _CD_RE.match(str(contract_date if contract_date is not None else "").strip().upper())
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    if not (1 <= mo <= 12) or y < 1900:
        return None
    kind, n = (m.group(3), int(m.group(4))) if m.group(3) else ("W", 3)
    wd = 2 if kind == "W" else 4                        # Monday=0：週三=2、週五=4
    first = date(y, mo, 1)
    day = first + timedelta(days=(wd - first.weekday()) % 7 + 7 * (n - 1))
    if day.month != mo:
        return None
    for _ in range(31):                                 # 順延上限（實務上最多數天）
        if _trading(day, holidays):
            return day.isoformat()
        day += timedelta(days=1)
    return None


def _cd(r) -> str:
    return str(r.get("contract_date") if r.get("contract_date") is not None else "").strip()


def expiring_contracts(rows: Iterable[dict], d: str, holidays=None) -> tuple[set[str], list[str]]:
    """rows 中「到期日＝d」的 contract_date 集合，與無法解析（不排除）的 contract_date 清單（排序、去重）。"""
    ex: set[str] = set()
    bad: set[str] = set()
    for c in {_cd(r) for r in rows or []}:
        e = contract_expiry(c, holidays)
        if e is None:
            bad.add(c or "<空>")
        elif e == d:
            ex.add(c)
    return ex, sorted(bad)


def pc_ratios(rows: Iterable[dict], exclude: Iterable[str] = ()) -> dict:
    """TXO Put/Call 比。

    OI 比：只取 trading_session=="position"（盤後時段 OI 為 0），且不計 exclude 內的 contract_date
    （當日到期契約，§0b）；成交量比：position＋after_market 兩時段合計、**不排除**。
    回傳 {pc_oi, pc_vol, put_oi, call_oi, put_vol, call_vol, n, ex_oi}；0 筆 → 比值與口數全 None。
    ex_oi＝{contract_date: {put, call}} 被排除的口數（log 用）。
    """
    ex = {str(c).strip() for c in exclude}
    ex_oi: dict[str, dict] = {}
    put_oi = call_oi = put_vol = call_vol = 0.0
    n = 0
    for r in rows or []:
        cp = _cp(r.get("call_put"))
        if cp is None:
            continue
        sess = str(r.get("trading_session") or "").strip()
        if sess not in ("position", "after_market"):
            continue
        n += 1
        vol = _num(r.get("volume"))
        if cp == "put":
            put_vol += vol
        else:
            call_vol += vol
        if sess == "position":
            oi = _num(r.get("open_interest"))
            c = _cd(r)
            if c in ex:
                g = ex_oi.setdefault(c, {"put": 0, "call": 0})
                g[cp] += int(oi)
                continue
            if cp == "put":
                put_oi += oi
            else:
                call_oi += oi
    if n == 0:
        return {"pc_oi": None, "pc_vol": None, "put_oi": None, "call_oi": None,
                "put_vol": None, "call_vol": None, "n": 0, "ex_oi": {}}
    return {"pc_oi": _ratio_pct(put_oi, call_oi), "pc_vol": _ratio_pct(put_vol, call_vol),
            "put_oi": int(put_oi), "call_oi": int(call_oi),
            "put_vol": int(put_vol), "call_vol": int(call_vol), "n": n,
            "ex_oi": {k: ex_oi[k] for k in sorted(ex_oi)}}


def mtx_open_interest(mtx_rows: Iterable[dict], exclude: Iterable[str] = ()) -> dict:
    """小台全市場未平倉（position 時段）。兩種口徑都回：

    all＝全部契約（主值候選）；monthly＝排除 contract_date 含 "W" 的週契約（形如 202609W5）。
    兩者都不計 exclude 內的 contract_date（當日到期契約，§0b）；ex_oi＝{contract_date: 被排除口數}。
    """
    ex = {str(c).strip() for c in exclude}
    ex_oi: dict[str, int] = {}
    tot = mon = 0.0
    n = 0
    for r in mtx_rows or []:
        if str(r.get("trading_session") or "").strip() != "position":
            continue
        fid = r.get("futures_id")
        if fid is not None and str(fid).strip() not in ("", "MTX"):
            continue
        n += 1
        oi = _num(r.get("open_interest"))
        c = _cd(r)
        if c in ex:
            ex_oi[c] = ex_oi.get(c, 0) + int(oi)
            continue
        tot += oi
        if "W" not in str(r.get("contract_date") or "").upper():
            mon += oi
    if n == 0:
        return {"all": None, "monthly": None, "n": 0, "ex_oi": {}}
    return {"all": int(tot), "monthly": int(mon), "n": n, "ex_oi": {k: ex_oi[k] for k in sorted(ex_oi)}}


def retail_ratio(mtx_rows: Iterable[dict], inst_rows: Iterable[dict], main: str | None = None,
                 exclude: Iterable[str] = ()) -> dict:
    """小台散戶多空比。

    法人多／空＝三大法人 long/short_open_interest_balance_volume 加總；
    散戶淨部位＝法人空 − 法人多（口）；散戶多空比＝散戶淨部位 ÷ 全市場未平倉 ×100（%，兩位）。
    main：全市場主值口徑（預設 MTX_OI_MAIN）。任一側 0 筆 → 該側欄位 None、比值 None。
    exclude：全市場未平倉不計的 contract_date（當日到期契約，§0b）；法人多空無契約維度、不受影響。
    """
    main = main or MTX_OI_MAIN
    lg = sh = 0.0
    ni = 0
    for r in inst_rows or []:
        fid = r.get("futures_id")
        if fid is not None and str(fid).strip() not in ("", "MTX"):
            continue
        ni += 1
        lg += _num(r.get("long_open_interest_balance_volume"))
        sh += _num(r.get("short_open_interest_balance_volume"))
    oi = mtx_open_interest(mtx_rows, exclude)
    out = {"mtx_oi": oi["all"] if main == "all" else oi["monthly"],
           "mtx_oi_all": oi["all"], "mtx_oi_monthly_only": oi["monthly"], "mtx_ex_oi": oi["ex_oi"],
           "inst_long": None, "inst_short": None, "retail_net": None, "retail_ratio": None,
           "n_inst": ni, "n_mtx": oi["n"]}
    if ni:
        out["inst_long"], out["inst_short"] = int(lg), int(sh)
        out["retail_net"] = int(sh - lg)
        if out["mtx_oi"]:
            out["retail_ratio"] = _ratio_pct(sh - lg, out["mtx_oi"])
    return out


# ════════════════════════════════════════════════════════════════
# 抓取
# ════════════════════════════════════════════════════════════════

def _records(df) -> list[dict]:
    if df is None:
        return []
    if isinstance(df, list):
        return df
    return df.to_dict("records") if not df.empty else []


def _default_fetch(dataset: str, **params):
    from finmind import fm_get  # lazy：純函式路徑與測試不碰網路
    return fm_get(dataset, **params)


def compute_day(d: str, fetch: Callable | None = None, holidays=None) -> dict:
    """算單日一列。fetch(dataset, **params) 回 DataFrame／list（0 筆＝空）／None（請求失敗）。

    holidays：twse_holidays.TwseHolidays 或 None（行事曆未載入＝到期日只排週末，fail-open）；
    由 update() 每班讀一次後傳入。未平倉排除「到期日＝d」的契約（§0b）。

    任一資料集 0 筆 → 對應欄位 null（不整日丟棄）；請求失敗（None）→ 拋 SentimentFetchError，
    由 update() 停在這一天、下一班再來（不把「沒抓到」寫成「沒有資料」而永久沾黏）。
    """
    if not YMD_RE.match(d or ""):
        raise ValueError(f"日期格式錯誤：{d!r}")
    fetch = fetch or _default_fetch
    q = {"start_date": d, "end_date": d}
    got = {}
    for key, ds, extra in (("vix", "TaiwanOptionVix", {}),
                           ("txo", "TaiwanOptionDaily", {"data_id": "TXO"}),
                           ("mtx", "TaiwanFuturesDaily", {"data_id": "MTX"}),
                           ("inst", "TaiwanFuturesInstitutionalInvestors", {"data_id": "MTX"})):
        res = fetch(ds, **extra, **q)
        if res is None:
            raise SentimentFetchError(f"{ds} {d} 請求失敗")
        got[key] = [r for r in _records(res) if str(r.get("date") or d)[:10] == d]
    v = vix_close(got["vix"])
    ex_txo, bad_txo = expiring_contracts(got["txo"], d, holidays)
    ex_mtx, bad_mtx = expiring_contracts(got["mtx"], d, holidays)
    pc = pc_ratios(got["txo"], ex_txo)
    rt = retail_ratio(got["mtx"], got["inst"], exclude=ex_mtx)
    row = {"date": d, "vix": v["vix"],
           "pc_oi": pc["pc_oi"], "pc_vol": pc["pc_vol"],
           "put_oi": pc["put_oi"], "call_oi": pc["call_oi"],
           "put_vol": pc["put_vol"], "call_vol": pc["call_vol"],
           "mtx_oi": rt["mtx_oi"], "mtx_oi_monthly_only": rt["mtx_oi_monthly_only"],
           "inst_long": rt["inst_long"], "inst_short": rt["inst_short"],
           "retail_net": rt["retail_net"], "retail_ratio": rt["retail_ratio"],
           # 行事曆讀不到（fail-open 只排週末）時，休市順延的到期日可能判錯 → 記 cv=1，下一班讀得到行事曆時自動重算
           "cv": SENTIMENT_CALC_VER if holidays is not None else 1}
    # 首跑比對用（小台全市場口徑未定案）：兩種口徑與 VIX 取樣時點印一行（無 token）
    logger.info(f"sentiment {d}: VIX={v['vix']}@{v['time']}（{v['n']} 筆）｜P/C OI={pc['pc_oi']}% "
                f"Vol={pc['pc_vol']}%｜MTX 全市場 OI 全部契約={rt['mtx_oi_all']} 僅月契約={rt['mtx_oi_monthly_only']}"
                f"（主值口徑={MTX_OI_MAIN}）｜法人多={rt['inst_long']} 空={rt['inst_short']} "
                f"散戶淨={rt['retail_net']} 比={rt['retail_ratio']}%")
    txo_ex = "、".join(f"{c}(put {g['put']}／call {g['call']})" for c, g in pc["ex_oi"].items()) or "無"
    mtx_ex = "、".join(f"{c}(OI {n})" for c, n in rt["mtx_ex_oi"].items()) or "無"
    cal_note = "" if holidays is not None else "（行事曆未載入，到期日只排週末；遇休市順延會算錯）"
    logger.info(f"sentiment {d}: 未平倉排除當日到期契約 TXO {txo_ex}｜MTX {mtx_ex}｜"
                f"無法解析不排除 TXO {len(bad_txo)} 個{bad_txo[:5]} MTX {len(bad_mtx)} 個{bad_mtx[:5]}{cal_note}")
    return row


# ════════════════════════════════════════════════════════════════
# 期交所交叉核對（非主資料源；連不到就跳過）
# ════════════════════════════════════════════════════════════════

def _taifex_date(s) -> str | None:
    s = re.sub(r"[^0-9]", "", str(s or ""))
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else None


def parse_taifex_pc(items) -> dict[str, dict]:
    """期交所 OpenAPI PutCallRatio → {date: {pc_oi, pc_vol}}。欄名以關鍵字比對（OI／Volume＋Ratio）。"""
    out: dict[str, dict] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        d = None
        oi = vol = None
        for k, v in it.items():
            kl = str(k).lower()
            if d is None and ("date" in kl or "日期" in kl):
                d = _taifex_date(v)
            elif "ratio" in kl or "比" in kl:
                try:
                    f = float(str(v).replace("%", "").replace(",", ""))
                except ValueError:
                    continue
                # 官方欄名 PutCallVolumeRatio% / PutCallOIRatio%；先判 volume（其欄名不含 "oi"）
                if "vol" in kl or "成交" in kl:
                    vol = f
                elif "oi" in kl or "未平倉" in kl:
                    oi = f
        if d and (oi is not None or vol is not None):
            out[d] = {"pc_oi": oi, "pc_vol": vol}
    return out


def _default_taifex():
    import requests
    r = requests.get(TAIFEX_PC_URL, timeout=15, headers={"Accept": "application/json"})
    r.raise_for_status()
    return r.json()


def taifex_check(rows: list[dict], taifex_fetch: Callable | None = None) -> dict:
    """取期交所與本檔**共同最新日**逐位比對 pc_oi／pc_vol（兩位小數相等＝match）。"""
    try:
        official = parse_taifex_pc((taifex_fetch or _default_taifex)())
    except Exception as e:  # 連不到／格式不符：跳過，不影響產出
        msg = mask_secret(e)[:200]
        logger.info(f"sentiment 期交所交叉核對略過：{msg}")
        return {"date": None, "pc_oi": None, "pc_vol": None, "match": None,
                "note": f"unreachable：連不到期交所 OpenAPI（{type(e).__name__}）"}
    mine = {r["date"]: r for r in rows if r.get("pc_oi") is not None}
    common = sorted(set(official) & set(mine))
    if not common:
        logger.info("sentiment 期交所交叉核對：無共同日期")
        return {"date": None, "pc_oi": None, "pc_vol": None, "match": None,
                "note": "期交所回傳與本檔無共同日期"}
    d = common[-1]
    o, m = official[d], mine[d]
    eq = lambda a, b: a is not None and b is not None and f"{a:.2f}" == f"{b:.2f}"  # noqa: E731
    match = eq(o["pc_oi"], m["pc_oi"]) and eq(o["pc_vol"], m["pc_vol"])
    logger.info(f"sentiment 期交所交叉核對 {d}：官方 OI={o['pc_oi']} Vol={o['pc_vol']}｜本檔 OI={m['pc_oi']} "
                f"Vol={m['pc_vol']} → match={match}")
    return {"date": d, "pc_oi": o["pc_oi"], "pc_vol": o["pc_vol"], "match": match}


# ════════════════════════════════════════════════════════════════
# 檔案與補洞
# ════════════════════════════════════════════════════════════════

def load(path: Path | None = None) -> dict:
    path = path or SENTIMENT_PATH
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and isinstance(doc.get("rows"), list):
            return doc
    except Exception:
        pass
    return {"schema": SCHEMA, "start": SENTIMENT_START, "rows": []}


def row_cv(r: dict) -> int:
    """列的計算版本；缺鍵或非整數（含 bool）視為 1。"""
    v = r.get("cv") if isinstance(r, dict) else None
    return v if isinstance(v, int) and not isinstance(v, bool) else 1


def plan_dates(calendar: list[str], rows: list[dict], max_days: int) -> list[str]:
    """本班要算的日期（升序）：

    ①**最新交易日**一律重算（同日覆寫；晚到的資料由此補上，期交所核對也需要它）；
    ②最近 SENTIMENT_REFRESH_DAYS 個交易日中有 null 關鍵欄位者重算；
    ③其餘缺漏日由舊到新補，總數不超過 max_days；**cv 缺或 < SENTIMENT_CALC_VER 的既有列視同缺漏**
      （計算規則改版後的歷史重算，§0b），與真缺漏日一起由舊到新排。
    """
    cal = sorted({d for d in calendar if YMD_RE.match(str(d)) and d >= SENTIMENT_START})
    if not cal or max_days <= 0:
        return []
    have = {r.get("date"): r for r in rows}
    must = {cal[-1]}
    for d in cal[-SENTIMENT_REFRESH_DAYS:]:
        r = have.get(d)
        if r is not None and any(r.get(k) is None for k in KEY_FIELDS):
            must.add(d)
    must = sorted(must)[-max_days:]
    rest = [d for d in cal if (d not in have or row_cv(have[d]) < SENTIMENT_CALC_VER) and d not in must]
    picked = set(must) | set(rest[:max(0, max_days - len(must))])
    return sorted(picked)


def merge_rows(rows: list[dict], new: list[dict]) -> list[dict]:
    """依日期合併（同日以新值覆寫、不重複），升序。"""
    m = {r["date"]: r for r in rows if isinstance(r, dict) and YMD_RE.match(str(r.get("date") or ""))}
    for r in new:
        m[r["date"]] = r
    return [m[d] for d in sorted(m)]


def latest_date(doc: dict) -> str | None:
    """有任一關鍵值的最新列日期（給 status.json.sources.sentiment）。"""
    for r in reversed(doc.get("rows") or []):
        if any(r.get(k) is not None for k in KEY_FIELDS):
            return r.get("date")
    return None


def write(doc: dict, path: Path | None = None) -> None:
    path = path or SENTIMENT_PATH
    path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def update(calendar: list[str], *, fetch: Callable | None = None, taifex_fetch: Callable | None = None,
           path: Path | None = None, max_days: int = SENTIMENT_MAX_BACKFILL,
           dates: list[str] | None = None, dry_run: bool = False,
           budget_sec: float = SENTIMENT_BUDGET_SEC, clock: Callable[[], float] = time.monotonic,
           holidays=_LOAD) -> dict:
    """補齊 data/sentiment.json 並寫回；回傳寫出的 doc。

    單日請求失敗 → 該日不寫入（仍是「缺」，下一班重試，不會被寫成 null 而沾黏），繼續下一天；
    連續 SENTIMENT_MAX_CONSEC_FAIL 天失敗即放棄本班（API 掛掉時不空轉）。已算好的照寫，
    最後拋 SentimentFetchError（訊息已遮罩）讓呼叫端記錄。

    牆鐘預算：每算完一天（不論成敗）檢查 clock() 自進入本函式起的耗時，達 budget_sec 且還有
    剩餘天數即停止本班——已算好的照寫、**不拋錯**（用完預算不算失敗），剩餘天數仍是「缺」，
    下一班由 plan_dates 自然補回。clock 可注入供測試。

    holidays：到期日推算用的休市行事曆。預設（未傳）＝本班有日子要算時呼叫 twse_holidays.load()
    **讀一次**，讀不到回 None＝fail-open 只排週末（log 一行）；測試可直接注入 TwseHolidays 或 None。
    """
    t0 = clock()
    doc = load(path)
    todo = sorted(dates) if dates is not None else plan_dates(calendar, doc["rows"], max_days)
    logger.info(f"sentiment 本班計算 {len(todo)} 天：{todo[0] + '~' + todo[-1] if todo else '（無）'}")
    if holidays is _LOAD:
        holidays = twse_holidays.load() if todo else None
        if todo and holidays is None:
            logger.warning("sentiment 休市行事曆未載入，契約到期日只排週末（遇國定假日順延的到期日會算錯）")
    new: list[dict] = []
    err: Exception | None = None
    failed: list[str] = []
    consec = 0
    for d in todo:
        try:
            new.append(compute_day(d, fetch, holidays))
            consec = 0
        except Exception as e:
            err = e
            failed.append(d)
            consec += 1
            logger.warning(f"sentiment {d} 失敗：{mask_secret(e)}")
            if consec >= SENTIMENT_MAX_CONSEC_FAIL:
                logger.warning(f"sentiment 連續 {consec} 天失敗，放棄本班其餘 {len(todo) - todo.index(d) - 1} 天")
                break
        i = todo.index(d)
        elapsed = clock() - t0
        if elapsed >= budget_sec and i < len(todo) - 1:
            logger.warning(f"sentiment 本班已用 {elapsed:.0f} 秒（預算 {budget_sec:.0f} 秒），停止；"
                           f"剩餘 {len(todo) - i - 1} 天（{todo[i + 1]}~{todo[-1]}）留待下班")
            break
    rows = merge_rows(doc["rows"], new)
    out = {"schema": SCHEMA, "generated_at": datetime.now(TPE).isoformat(timespec="seconds"),
           "start": SENTIMENT_START, "rows": rows,
           "check": {"taifex_pc": taifex_check(rows, taifex_fetch)}}
    if not dry_run:
        write(out, path)
    if err is not None:
        raise SentimentFetchError(mask_secret(f"{len(failed)} 天失敗（{', '.join(failed)}）：{err}"))
    return out


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="只算（並覆寫）這一天 YYYY-MM-DD")
    ap.add_argument("--backfill", type=int, default=SENTIMENT_MAX_BACKFILL, help="本次最多補幾天")
    ap.add_argument("--dry-run", action="store_true", help="算完只印、不寫檔")
    a = ap.parse_args(argv)
    try:
        cal = json.loads((DATA / "meta.json").read_text(encoding="utf-8")).get("calendar", [])
    except Exception:
        cal = []
    out = update(cal, max_days=a.backfill, dates=[a.date] if a.date else None, dry_run=a.dry_run)
    if a.dry_run:
        print(json.dumps(out["rows"][-3:], ensure_ascii=False, indent=1))
        print(json.dumps(out["check"], ensure_ascii=False))


if __name__ == "__main__":
    main()
