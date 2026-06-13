# taiwan-flows spec V1(2026-06-11 定稿)

三大法人流向看板:ETF / 投信進出 / 外資進出 / 同步買賣超。
獨立 repo `shihpc/taiwan-flows`,`taiwan-stock-radar` 之後以其 JSON 為資料源。

---

## 1. 架構

```
FinMind API(主)+ TWSE/TAIFEX OpenAPI(補缺)
        ↓
GitHub Actions
  ├ cron:每交易日 17:30 台北時間(09:30 UTC,Mon–Fri,程式內判斷非交易日跳過)
  └ workflow_dispatch:GitHub 手機 App 手動觸發
        ↓
Python pipeline:fetch → 清洗 → 投信庫存累計 → 預算 latest → commit JSON
        ↓
GitHub Pages 靜態前端(mobile-first,單一 index.html)
```

## 2. 資料源對照

| 資料 | 來源 | Dataset |
|---|---|---|
| 投信/外資/自營買賣超(張、金額) | FinMind | TaiwanStockInstitutionalInvestorsBuySell |
| 股價 OHLC、成交量、成交金額 | FinMind | TaiwanStockPrice |
| 外資持股張數/比率 | FinMind | TaiwanStockShareholding |
| 投信庫存 | 推算 | baseline_20260430.json + 每日買賣超累計 |
| 期貨三大法人未平倉 | FinMind | TaiwanFuturesInstitutionalInvestors |
| ETF 規模/受益單位 | TWSE OpenAPI | 實作時驗證;抓不到則以持股市值與成交金額替代 |
| 股票/ETF 清單、產業別 | FinMind | TaiwanStockInfo(ETF 判別:股票代號 00 開頭) |

**投信庫存累計規則**:`inv(t) = inv(t-1) + 投信買賣超張數(t)`,下限 0。
baseline 為 2026-04-30(隨附 baseline_20260430.json,含 2,281 檔庫存與發行張數)。
缺 2026-05-01 ~ 上線日的買賣超 → 上線首跑回補此區間後累計至今。
校正機制:保留 `--rebase <new_baseline.json>` 指令,日後丟新 Excel 萃取檔即可重設基準。

## 3. 儲存設計(repo 內)

```
data/
├ daily/YYYYMMDD.json      每日逐檔明細(見 schema)
├ futures/YYYYMMDD.json    期貨三大法人
├ meta.json                交易日曆、代號↔名稱、發行張數、is_etf 旗標
├ baseline_20260430.json   投信庫存基準(不動)
└ latest.json              最新交易日四頁預算結果(開頁秒載)
└ latest_ranges.json       近 5/10/20/65 交易日四頁預算結果(常用區間秒開)
```

### daily/YYYYMMDD.json schema(陣列式緊湊格式,省 60% 體積)

```json
{
  "date": "2026-06-11",
  "cols": ["code","close","chg_pct","vol","amt",
           "t_net","t_amt","f_net","f_amt","d_net","d_amt",
           "t_inv","f_shares","f_pct"],
  "rows": [["2330",1050.0,-2.06,32500,34125000, 596.0,1291266, -1200.5,-1260525, 0,0, 808059.2,73.1,73.1], ...]
}
```

單位:張(net/inv/shares)、千元(amt)、%(pct)。
估算:2,281 檔 × 14 欄 ≈ 350KB,gzip ~80KB。Pages 自帶 gzip 傳輸。

### 歷史保留(預設,可調)

- 上線時回補 **65 個交易日** 的買賣超/股價類資料(可立即拉滿一季區間;庫存欄位自 2026-05-01 起才有效)
- 之後每日累積、永久保留;repo 年增約 90MB,兩年後再評估 squash 或搬 Release assets

## 4. 計算邏輯

### 4.1 通用:單日 vs 區間聚合口徑

| 欄位 | 單日 | 區間 [d1, d2] |
|---|---|---|
| 買賣超張/金額 | 當日值 | Σ 逐日 |
| 投信庫存、外資持股、持股比率 | 當日值 | **d2 末日值** |
| 持股變動% | 買賣超張 ÷ 發行張數 | Σ買賣超張 ÷ 發行張數 |
| 漲跌幅 | 當日 % | (close(d2) ÷ close(d1 前一交易日)) − 1 |
| 法人佔成交量 | 買賣超張 ÷ 成交量 | Σ買賣超張 ÷ Σ成交量 |
| 乖離月線% | 當日值 | d2 末日值 |
| 期貨未平倉 | 當日淨額 | d2 淨額,附 vs d1 前一日增減 |

區間模式由前端抓取區間內 daily 檔在瀏覽器端聚合。**區間上限 65 交易日**(涵蓋一季,超過提示縮短)。

**預算常用區間**(降低一季區間載入延遲):pipeline 額外輸出 `latest_ranges.json`,內含近 5/10/20/65 交易日的四頁預算結果,常用區間秒開;僅自訂任意區間才走前端逐日 fetch + 進度條。

### 4.2 ETF 頁

母體:meta.is_etf == true(約 400 檔),分 債券型 / 非債券型(依 TaiwanStockInfo industry_category 判別)。

1. **整體統計卡**:整體/非債券型/債券型 → 市值合計、成交金額合計、外資/投信/自營/其他 淨買賣金額。其他 = 0 −(外資+投信+自營)淨額(法人對手盤近似)。
2. **交易量排行 Top 20**:依成交金額排序;欄位 = 成交金額、外資買/賣金額、自營買/賣金額、其他買/賣(= 總成交 − 法人買賣)。
3. **市值排行 Top 20**:依市值排序;欄位 = 市值、外資/投信/自營持股市值、其他 = 市值 − 三法人持股市值。

### 4.3 投信進出頁

母體:全部 2,281 檔(**不排除 ETF、不設門檻**)。買超/賣超雙向,各 Top 30。

- **排行 A|持股變動率**:|持股變動%| 排序
- **排行 B|買賣超金額**:|買賣超金額| 排序
- 共同欄位:代號、名稱、買賣超金額(千)、買賣超(張)、持股變動%、持股比率%、持股(張)、持股市值(百萬)、投信佔成交量、漲跌%、乖離月線%

持股市值 = 庫存 × 收盤價;投信持股比率 = 庫存 ÷ 發行張數(已驗證與 CMoney 口徑一致,誤差 < 0.01pp)。

### 4.4 外資進出頁

邏輯同 4.3,改用外資欄位(持股直接取 FinMind Shareholding,不需累計)。
頁首加 **台指期外資未平倉卡**:未平倉口數淨額、金額淨額(千)、vs 前月底增減(月底 = 該月最後交易日 futures 檔)。

### 4.5 同步買賣超頁

- **同步買超**:投信淨額 > 0 且 外資淨額 > 0(區間模式用區間累計值),依加總金額排序 Top 30
- **同步賣超**:兩者皆 < 0,同上
- 欄位:投信金額/張數、外資金額/張數、加總金額/張數、漲跌%
- **成交量佔比排行**:投信佔比 Top 10、外資佔比 Top 10(佔比 = |買賣超張| ÷ 成交量)

## 5. 前端

- 單一 `index.html` + vanilla JS(沿用 radar 前端模式),深色終端風格
- 頂部:四 tab + 模式切換(單日 ⇄ 區間)+ 日期選擇器(date input,區間為雙 date)
- 表格:手機橫向可滑,凍結代號/名稱欄;數字紅漲綠跌(台股慣例)
- 個股連結 → Yahoo Finance(沿用 radar 的做法,避開 TradingView 嵌入限制)
- 載入策略:預設讀 latest.json 秒開;近 5/10/20/65 日讀 latest_ranges.json 秒開;自訂任意區間才 fetch daily 檔並顯示進度條,fetch 後 sessionStorage 級記憶體快取

## 6. Actions workflow

```yaml
on:
  schedule: [{cron: "30 9 * * 1-5"}]   # 17:30 TPE
  workflow_dispatch: {}
```

步驟:checkout → pip install → `python pipeline.py`(判斷交易日;fetch;累計;產出)→ commit & push(`[skip ci]`)。
Secrets:`FINMIND_TOKEN`。
失敗通知:Actions 預設 email;資料缺漏(FinMind 尚未更新)時 retry 3 次間隔 10 分鐘,仍失敗則寫入 `data/status.json` 供前端顯示「資料未更新」。

## 7. 實作順序

1. 建 repo、放 baseline、meta.json 產生器(TaiwanStockInfo)
2. pipeline.py:單日 fetch + daily.json 輸出 + 庫存累計
3. 回補 2026-05-01 起至今 + 往前共 65 交易日
4. latest.json + latest_ranges.json 預算邏輯(四頁;近 5/10/20/65 日)
5. 前端四 tab(先單日模式)
6. 區間模式(前端聚合)
7. Actions 排程上線、手機 dispatch 測試

驗收:任選一日與 CMoney 工作簿比對投信/外資排行前 10 名,代號一致、金額誤差 < 1%。
