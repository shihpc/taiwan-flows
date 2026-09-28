# 籌碼雷達 tab — 驗收條件

**寫於** 2026-09-28，動手前定稿。**目標專案**：`/home/user/taiwan-flows`，分支 `claude/investment-site-optimization-nac77h`。
**動機**：重建 CMoney 籌碼K線的兩個核心視圖，只用家族已授權資料。設計示意：https://claude.ai/artifact/ETr466nR5q9ox7eVY1Jsft （第一分頁）。
**性質**：純描述性顯示（鐵律 8：只需驗算式正確）。象限分類**不進任何排序、訊號或顏色以外的強調**；免責句必留。

## 0. 硬約束
| # | 約束 | 驗法 |
|---|---|---|
| R1 | **只改 `index.html`＋文件＋測試**；後端 `src/*.py`、`data/*`、workflow 零改動 | `git diff --stat` |
| R2 | 不動既有聚合函式（`aggregateRange`／`jPage*`／`buildSectorStocks` 等）；`python tests/parity.py --n 1 5 10 20 65` 仍零差異 | 實跑 |
| R3 | 單一 `index.html`、無 build、無新外部 script；**CSP 不需改**（資料全同源） | grep CSP 行未變 |
| R4 | 所有外來字串進 innerHTML 一律 `esc()`（類股名、股名來自 FinMind） | 讀碼＋注入測試 |
| R5 | 首屏不多載任何檔：新 tab 資料只在切到該 tab 時 lazy 載入（沿用 `lazyJson()`） | Playwright 記錄首屏請求清單與改動前相同 |
| R6 | 顏色走台股慣例且只用既有 token：流入＝紅系、流出＝綠系 | 讀碼 |

## 1. 功能
新 tab：`data-tab="radar"`、標籤「籌碼雷達」，排在 `chain` 之後。hash 路由自動納入（tab 清單由 DOM 取）；本 tab 新增的 hash key 只有 `rcls`（`exchange`｜`chain`）與 `rinv`（`total`｜`foreign`｜`trust`｜`dealer`），白名單＋非法值靜默退回預設，只輸出非預設值。

### 1.1 類股資金四象限（上半）
- 資料：`sector_ranges_lite.json` 的 `windows.r5` 與 `windows.r20`（**不用** full 檔），分類法 `rcls`、法人別 `rinv`。
- 只取兩窗都有、且 r20 的 `n >= 8` 的類股。
- `x = r20.net_amt_k / 20 / 1e5`（億／日）；`y = r5.net_amt_k / 5 / 1e5 − x`。r5、r20 的交易日數**從該窗 `trading_days` 取**，不寫死 5／20。
- 象限：`x≥0,y≥0` 加速流入；`x≥0,y<0` 流入放緩；`x<0,y<0` 加速流出；`x<0,y≥0` 流出放緩。
- 散佈圖：inline SVG、兩軸 signed-sqrt 刻度（圖說寫明）、點大小依 `n`、最多標 8 個名稱且標籤不重疊、`<title>` tooltip 顯示類股／象限／近 20 日與近 5 日淨額（億，一位小數，千分位）。
- 旁表：類股｜象限｜近 20 日 億｜近 5 日 億，預設依近 20 日淨額排序，欄位可點排序（沿用 `tbl()`，排序帶次鍵 `sector`）。
- 標頭徽章：兩窗起訖日、資料日、類股數。chain 分類時沿用既有「多對多、非市佔、不含 ETF」徽章文案。

### 1.2 千張大戶持股週變化（下半）
- 資料：同源 `../postmkt/data/diag/diag.json`（GitHub Pages 同 origin `shihpc.github.io`），**只在本 tab 首次開啟時載入一次**。讀不到（本機 http.server、404、JSON 壞）→ 該區塊顯示一行中性灰「讀不到大戶資料（postmkt 診斷素材庫）」，上半照常。
- 欄位：`stocks[code].hd`（`[前週, 本週]`，取末值為本週大戶持股%）、`hdw`（週變化 pp）、`hdd`（集保資料日）、`f5`／`t5`、`n`、`ind`。
- 兩張表：增加最多 20 檔、減少最多 20 檔；欄＝代號＋股名（代號連 Yahoo，沿用 `yahoo()`）｜產業｜大戶持股%｜週變化 pp（兩位小數）｜外資 5 日張｜投信 5 日張。
- 可選篩選：產業下拉（取 diag 內出現的 `ind`，預設「全部」）。
- 標頭徽章：集保資料日（取全體 `hdd` 的 max）、法人資料日（`diag.date`）、涵蓋檔數（有 `hdw` 的檔數）。**必寫**「涵蓋 postmkt 診斷素材庫 N 檔，非全市場」。
- 口徑說明一句：與籌碼K線「大戶／散戶買賣超」不同，這裡是集保分級千張大戶，週頻。

### 1.3 免責
tab 底部：「類股象限與大戶變化為現況描述，非買賣訊號；無回測依據。」

## 2. 驗收清單（fresh-context 驗收者逐條，綁 commit）
- [ ] A1 R1–R6 逐條
- [ ] A2 `python tests/parity.py --n 1 5 10 20 65` 零差異；既有 `python -m pytest tests/ -q`（若有）全綠
- [ ] A3 新增 `tests/test_radar.mjs`（node，沿用 `tests/extract_js.mjs` 抽函式的做法）：以真實 `data/sector_ranges_lite.json` 驗 x／y 算式、`n>=8` 過濾、四象限分類（含 x=0、y=0 邊界）；以合成 diag 驗排序、產業篩選、缺 `hdw` 的檔排除
- [ ] A4 Playwright（本機 http.server 於 repo 上一層目錄起，讓 `../postmkt/...` 可達；另測一次讀不到的降級）：9 個 tab 逐一點擊 console 零 error；本 tab 切 `rcls`／`rinv` 後圖表與表格更新、hash 寫入與還原正確、非法 hash 值退回預設
- [ ] A5 375／390／1280 三寬度：頁面 `scrollWidth == innerWidth`；SVG 不溢出；表格在自己的 `overflow-x:auto` 容器內
- [ ] A6 注入測試：把類股名與股名改成 `<img src=x onerror=...>` 餵進兩份 JSON，零觸發、字面顯示
- [ ] A7 首屏請求清單與改動前逐一相同（R5）
- [ ] A8 CLAUDE.md（tab 清單與 hash 節、跨 repo 讀 postmkt diag.json 這條新依賴）、README 同步；postmkt 端**不改檔**但在本檔與 CLAUDE.md 記「diag.json 多一個前端消費者」

## 3. 實作後補記（2026-09-28）
- **§1.2 欄位定義更正**：`hd` 在 postmkt `src/build_diag.py`（grep `o["hd"]`）是 **`[最新週%, 前一週%]`**，`hdw＝hd[0]−hd[1]`；
  本檔初稿寫成 `[前週, 本週]`／「取末值」是反的。實作取 **`hd[0]`** 為本週大戶持股%（`tests/test_radar.mjs` 有斷言守）。
- **增／減兩表的口徑**：「增加最多」只收 `hdw>0`、「減少最多」只收 `hdw<0`，各取前 20，同值以代號為次鍵；`hdw=0` 計入涵蓋數但不進兩表
  （產業篩選後檔數少時，避免零變化的檔被列進「增加最多」）。`hdw` 非有限數（缺、null、NaN）整檔排除。
- **集保資料日**取全體 `hdd` 的 max；若有檔的 `hdd` 較舊，說明句另報「其中 N 檔的集保資料日早於 …」。
- **跨 repo 依賴（postmkt 端不改檔）**：`postmkt/data/diag/diag.json` 多了一個前端消費者（本 repo 籌碼雷達 tab，同源相對路徑
  `../postmkt/data/diag/diag.json`）。消費欄位：頂層 `date`、`stocks[code]` 的 `hd`／`hdw`／`hdd`／`f5`／`t5`／`n`／`ind`；
  改名或改語意屬跨站變更。檔案約 745KB，只在切到本 tab 時載一次。
- **README**：本 repo 沒有 README.md（文件主體是 CLAUDE.md），A8 的 README 同步項以 CLAUDE.md 取代，未新建 README。
