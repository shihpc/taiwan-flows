# 市場情緒 tab — 驗收條件

**寫於** 2026-09-28，**2026-09-29 改寫**（前端改放 postmkt，比照籌碼雷達／社群聲量搬家的模式）。
**目標專案**：後端與資料＝`/home/user/taiwan-flows`；前端＝`/home/user/postmkt`（第 17 個 tab）。分支皆 `claude/investment-site-optimization-nac77h`。
**起點**：taiwan-flows `git stash@{0}`「sentiment-wip-for-postmkt」（`src/sentiment.py` 425 行 untracked＋`src/finmind.py` `mask_secret`），0 測試、未接 run_daily。
**動機**：補交易行為類情緒指標，與 taiwan-stock-news 的 PTT 社群聲量互補（一邊看交易、一邊看言論）。
**性質**：**純描述性顯示**（鐵律 8：只需驗算式正確）。不給多空判斷、不進任何排序或訊號；免責句必留。
使用者若日後要訊號或建議，另案裁決。

## 0. 已驗證的資料口徑（2026-09-28 Hetzner 實測，使用者 FinMind Sponsor token）

| 指標 | 資料集 | 口徑 | 實測證據（2026-09-24） |
|---|---|---|---|
| 臺指 VIX | `TaiwanOptionVix`（欄位 `date`／`time`／`vix`，分鐘級） | 每日取**當日最後一筆**（實測 `13:45:00`） | 23.12；**歷史最早 2026-03-02**，2024-06 查詢 0 筆＝資料集本身從此開始，不是截斷 |
| Put/Call 未平倉比 | `TaiwanOptionDaily` `data_id=TXO` | Σ put `open_interest` ÷ Σ call `open_interest`，**只取 `trading_session=="position"`**（盤後時段 OI 為 0） | 59,603÷69,848＝**85.33%**，與期交所官方 `PutCallOIRatio%` 逐位相同 |
| Put/Call 成交量比 | 同上 | Σ put `volume` ÷ Σ call `volume`，**`position`＋`after_market` 兩時段合計** | put 83,032＋42,942＝125,974、call 54,472＋49,543＝104,015 → **121.11%**，與官方逐位相同 |
| 小台散戶多空比 | `TaiwanFuturesDaily` `data_id=MTX`＋`TaiwanFuturesInstitutionalInvestors` `data_id=MTX` | 散戶淨部位＝法人空單合計－法人多單合計（三大法人 `short/long_open_interest_balance_volume` 加總）；**散戶多空比＝散戶淨部位 ÷ 全市場未平倉量**（全市場＝`trading_session=="position"` 的 `open_interest` 加總） | 法人多 3,304、空 10,719 → 散戶淨 **+7,415 口**；全市場 OI 待首跑取得 |

- **未定口徑（首跑要比對）**：小台全市場未平倉是否含週契約（`contract_date` 形如 `202609W5`）。實作要**兩種都算**：主值＝**全部契約**；另存 `mtx_oi_monthly_only` 供比對。首跑後由使用者或主對話依公開來源擇一，改常數即可。
- 期交所 OpenAPI `https://openapi.taifex.com.tw/v1/PutCallRatio`（Hetzner 可達，回最近約 19 個交易日）**只作交叉核對**，不是主資料源；GitHub runner 能否連到未知，連不到時跳過、不影響產出。

## 0b. 未平倉口徑更正：排除當日到期契約（2026-09-30，使用者裁決「照建議」）

**實證**（`tools/diag_pc_oi.py`，run 36678263476）：09-29 FinMind TXO `position` 未平倉加總 put 92,423／call 128,671（71.83%），
期交所官方 put 47,553／call 63,148（75.30%）。扣掉 `contract_date=202609F4` 一個契約後，其餘九個契約加總與官方**逐口相同**。
`202609F4`＝9 月第 4 個週五（09-25）到期，09-25（中秋）、09-28（教師節）休市順延至 **09-29** 當日到期結算；
當日有成交（官方成交量含它、雙方成交量比同為 90.39%），但收盤結算後已不存在，官方未平倉不含它。09-24 無契約到期，故當時吻合。

**規則**（`src/sentiment.py`）：
- 未平倉相關欄位（`put_oi`／`call_oi`／`pc_oi`、`mtx_oi`／`mtx_oi_monthly_only`／`retail_ratio`）**排除「到期日＝資料日」的契約**；
  成交量欄位（`put_vol`／`call_vol`／`pc_vol`）**不變**；法人多空（`inst_long`／`inst_short`／`retail_net`）無契約維度、不變。
- 到期日由 `contract_date` 推算：`YYYYMM`（6 碼）＝該月第 3 個週三；`YYYYMMWn`＝該月第 n 個週三；`YYYYMMFn`＝該月第 n 個週五；
  推得之日若非交易日（週末或家族休市行事曆 `src/twse_holidays.py` 所列）**順延到下一個交易日**（09-25→跳過 09-26/27/28→09-29 ✓；
  `202610F2`＝10-09 國慶→順延 10-12）。其他形狀（價差 `202610/202611`、無法解析）**不排除**並計數記 log。
- 行事曆讀不到＝fail-open 只排週末（09-29 那種順延情形會算錯，log 註明）；**同一班只讀一次行事曆**。
- 小台（MTX）比照同一規則（無官方值可對帳，依同理推定；首跑 log 列出被排除的契約與口數供人工檢視）。
- **歷史重算**：每列新增 `cv`（計算版本，整數；本規則＝2，舊列無此鍵視為 1）。`plan_dates` 把 `cv<2` 的既有列視同缺漏、由舊到新重算，
  仍受 `SENTIMENT_MAX_BACKFILL`／`SENTIMENT_BUDGET_SEC` 約束；`cv` 為附加欄位，前端不讀、形狀相容。
- `tools/diag_pc_oi.py` 加候選口徑 `position,exclude_expiring` 並在 ★ 比對中出現，合併後再跑一次，以 09-24／09-29 兩日同時 ★ 為線上驗證。

**驗收（本節）**：①離線 fixture 以 run 36678263476 的 09-24／09-29 逐契約加總重建 → `pc_oi` 分別＝85.33、75.30，
put/call 口數＝59603/69848、47553/63148；②到期日推算單元測試（上列四例＋6 碼月契約 2026-09→09-16、2026-10→10-21、fail-open 情形）；
③`cv` 重算：舊列被重算、每班上限與預算仍生效；④成交量比與既有 fixture 不變；⑤M1–M3 不退化、parity 零差異；
⑥postmkt 口徑說明文字補「未平倉不含當日到期契約」，其餘前端不動；⑦合併後跑 diag，兩日 `exclude_expiring` 皆 ★，且下一班 daily 的 `check.taifex_pc.match` 為 true。

## 1. 硬約束
| # | 約束 | 驗法 |
|---|---|---|
| M1 | 既有產出零改動：`data/daily/*`、`latest*.json`、`sector_*`、`totals.json`、`futures/*`、`foreign_history.json` 不因本功能改變；`python tests/parity.py --n 1 5 10 20 65` 零差異 | 實跑＋diff |
| M2 | **情緒指標失敗不得拖垮每日管線**：`run_daily` 內呼叫、例外被捕捉，寫入 `status.json` 的 `sources.sentiment`（資料日）與 `sentiment_error`（遮罩後訊息），印 `::warning::`；run_daily 的 exit code 語意不變 | 測試模擬例外 |
| M3 | token 不進 log／產物／例外訊息（FinMind token 走 query string，例外訊息會帶 URL，必須遮罩） | 測試注入假 token |
| M4 | postmkt 前端仍為單一 `index.html`、無新外部 script、**CSP 不改**（資料以同源相對路徑 `../taiwan-flows/data/sentiment.json` 讀，`connect-src 'self'` 已涵蓋）；taiwan-flows `index.html` **零改動** | diff |
| M5 | postmkt 首屏不多載：`sentiment.json` 只在切到本 tab 時載入（比照 `ensureCrSect`／social 的 lazy 模式） | Playwright 首屏請求清單與改動前相同 |
| M6 | 純描述：不出現「偏多／偏空／建議／訊號」等判斷字樣（免責句除外）；顏色只用中性色或既有 token，不以紅綠暗示方向 | grep＋讀碼 |
| M7 | postmkt 所有數字過 `esc()` 或型別保證（數值 `toFixed`），外來字串（日期）白名單 `^\d{4}-\d{2}-\d{2}$` | 讀碼 |

## 2. 後端（taiwan-flows）
- 新檔 `src/sentiment.py`：
  - `compute_day(date) -> dict | None`：打上表 4 個請求（VIX、TXO、MTX daily、MTX 法人）算當日值；任一資料集當日 0 筆 → 該欄 `null`（不整日丟棄）。
  - 純函式（可離線測）：`vix_close(rows)`、`pc_ratios(rows)`、`retail_ratio(mtx_rows, inst_rows)`，分別回傳上表口徑，並回傳中間值（加總、口數）供除錯。
  - `update(calendar)`：讀既有 `data/sentiment.json`，補齊 `meta.calendar` 中 ≥`SENTIMENT_START`（`2026-03-02`）且尚未有的交易日（**逐日、升序、每次最多 `SENTIMENT_MAX_BACKFILL`＝20 天**，避免單班請求爆量；首次上線後幾班自然補完），再寫回。
  - CLI：`python src/sentiment.py [--date D] [--backfill N] [--dry-run]`。
- 產物 `data/sentiment.json`：
  ```json
  {"schema":1,"generated_at":"...+08:00","start":"2026-03-02",
   "rows":[{"date":"2026-09-24","vix":23.12,
            "pc_oi":85.33,"pc_vol":121.11,"put_oi":59603,"call_oi":69848,"put_vol":125974,"call_vol":104015,
            "mtx_oi":null,"mtx_oi_monthly_only":null,"inst_long":3304,"inst_short":10719,
            "retail_net":7415,"retail_ratio":null}],
   "check":{"taifex_pc":{"date":"2026-09-24","pc_oi":85.33,"pc_vol":121.11,"match":true}}}
  ```
  比值存百分比、兩位小數（`budget.jround` 語意）；`rows` 依日期升序、同日覆寫不重複。
- `run_daily.py`：在既有產出完成**之後**呼叫 `sentiment.update()`（M2）；`gather_sources()` 補 `sentiment` 最新日。
- `daily.yml`：commit 步驟納入 `data/sentiment.json`（照既有 pull --rebase 重試）；**其餘步驟不動**。

## 3. 前端（postmkt）tab `sentiment`「市場情緒」（排在 `social` 之後、`dates` 之前（新增的第 17 個 tab，`dates` 仍最後））
- 常數 `SENT_URL = "../taiwan-flows/data/sentiment.json"`；本機驗證同籌碼雷達：http.server 起在 `/home/user`、以 `/postmkt/` 開頁。
- 三張卡：VIX、Put/Call（未平倉比為主，成交量比為輔）、小台散戶多空比。每張：
  - 最新值＋**自帶資料日**；與前一筆的差；相對近 60 筆（不足 60 筆以實際筆數並註明）的位置，文字寫「近 N 筆有值資料第 P 百分位」；均值（序列跳過 null，故以「筆」計而非「日」）。
  - 折線圖（inline SVG，全部歷史），畫 60 筆有值資料均線（不足 60 筆不畫）；軸標籤只標實際出現的值範圍；**線條用中性色**。
  - 一行口徑說明（取 §0 表文字）。
- 小台卡另顯示：法人多／空、散戶淨部位口數、全市場未平倉（兩種口徑都列，標「口徑比對中」直到定案）。
- 底部免責：「情緒指標為現況描述，非買賣訊號；無回測依據。VIX 歷史自 YYYY-MM-DD 起。」——前半逐字固定；後半的日期**不寫死**，由前端取資料中第一個有 `vix` 值的列日期（先過日期白名單），無值時整個後半句省略（2026-09-30 改：原寫死 2026-03-02，但實際資料自 `meta.calendar` 起點 2026-03-11 起）。
- 資料缺、`null`、讀不到檔（含檔案尚未產生的 404）→ 對應卡片灰字說明，其餘卡照常；整檔讀不到時整段一句「資料尚未產生或讀取失敗」，不報錯。
- 百分位／差值／均值寫成純函式，另檔測試 `tests/test_sentiment_frontend.mjs`（pytest 代跑包裝，比照 `test_holidays_frontend.py`）。
- 不接休市行事曆、不做「今天」判斷：資料日照實顯示即可（避免第五軸同型問題）。

## 4. 驗收清單（fresh-context 驗收者逐條，綁 commit）
- [ ] N1 M1–M7 逐條
- [ ] N2 `python -m pytest tests/ -q` 全綠；新增 `tests/test_sentiment.py`：以 §0 的 2026-09-24 實測數字做 fixture，斷言 P/C 兩比值**逐位**等於 85.33／121.11、散戶淨部位 7,415、VIX 取當日最後一筆、週契約兩口徑、0 筆→null、token 遮罩、`update()` 升序補洞與每班上限、同日覆寫
- [ ] N3 taiwan-flows `python tests/parity.py --n 1 5 10 20 65` 零差異；postmkt `python -m pytest tests/ -q` 全綠（含 chipradar／social／holidays 既有測試）
- [ ] N4 Playwright（postmkt）：17 個 tab 逐一點擊 pageerror 零；本 tab 以 `page.route` 餵樣本 JSON，三張卡數字與樣本一致；缺值降級；hash `#tab=sentiment` 往返
- [ ] N5 375／390／1280 `scrollWidth == innerWidth`
- [ ] N6 首屏請求清單與改動前相同（M5）
- [ ] N7 文件同步：postmkt CLAUDE.md（tab 數 16→17、新節）、taiwan-flows CLAUDE.md（新產物＋跨站消費者 postmkt）、postmkt `docs/date-semantics.md` 補 `sentiment.json` 的 `generated_at`／`rows[].date`
- [ ] **線上（合併後）**：首班 daily.yml 跑完，`data/sentiment.json` 有當日列、`check.taifex_pc.match` 為 true 或註明連不到；小台兩種全市場口徑數字回報給使用者擇一
