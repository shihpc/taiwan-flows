# taiwan-flows — 開發接軌文件（給新 Session / 新對話）

<!-- CANON:BEGIN v1 -->
<!-- 唯一事實來源＝shihpc/claude-harness 的 CANON.md。以下區塊在五個 repo 的 CLAUDE.md 頂端
     有 byte-identical 逐字副本，由各 repo 的 .github/workflows/canon.yml 守門（比對 sha256）。
     改動流程：先改 claude-harness/CANON.md → 跑 tools/sync_canon.py 同步五份 → 更新守門 hash。
     不要只改單一 repo，CI 會擋下來。 -->

## 通用工作鐵律（五個 repo 逐字相同，勿單獨修改）

1. **機密**：token／金鑰一律走 `.env` 或 Actions secret，絕不寫進任何會 commit 的檔案、log 或
   對話輸出。commit 前用 `git diff --staged` 檢查有無夾帶金鑰樣式字串（`sk-ant-`、`ghp_`、`eyJ` 開頭）。
2. **指揮官不下場**：掃 repo、通讀 >300 行的檔、一次讀 >3 個檔、查網頁研究、批次改檔、
   驗收改過的東西——這六類一律派 subagent，主對話只收結論＋`檔案:行號`。
3. **先寫驗收條件再動手**：動手前先寫下目標專案完整路徑＋怎樣算完成＋怎麼驗。改完派
   fresh-context subagent 驗收——**改東西的 agent（含主對話自己）不得擔任驗收者**。
4. **不確定不亂說**：陳述事實（尤其技術細節、數字、外部服務的限制與行為）要嘛附佐證（官方
   文件、實測、`檔案:行號`），要嘛明說「這點我不確定，需要查證」，不可憑印象當確定講。
   區分「已驗證事實」與「推測」，推測要標明。
5. **一次只做一件事**：只做明確要求的那件事，做完給簡短結果；少主動丟一堆延伸提案。
6. **完成的定義**：驗收條件逐條打勾＋fresh-context subagent 驗過＋產物在使用者拿得到的位置。
   **沒實跑過不算完成**。涉及部署者另需 push＋部署 workflow 成功＋**線上驗證本次變更的具體內容**
   （破快取 raw URL／curl／瀏覽器實查），只寫在本機不算完成。
7. **push 前**：先 `git fetch`；`git log --oneline main..origin/main` 非空必須先看內容（訊息／
   時間戳／diff）。一般 push → rebase 整合，嚴禁直接覆蓋；force push 前若 origin 領先的 commit
   是真實新工作 → 停下來問，授權「這次 force push」不等於授權蓋掉 origin 所有領先 commit。
8. **新指標／訊號先問有沒有回測依據**，沒有就先驗證再上線；不做預測宣稱，只描述歷史統計
   傾向與局限。
9. **語言**：對話與文件用繁體中文；程式碼註解可中文，identifier 用英文。

> 判準細則、派工模板、教訓簿見 `shihpc/claude-harness`（private）。雲端 session 需 add_repo 才讀得到。
<!-- CANON:END v1 -->

三大法人資金流看板：**外資進出 / 投信進出 / 外資投信同步 / 外資投信對作 / ETF市值 / 外資買賣超** 六個分頁。
盤後資料 → GitHub Actions 每日抓取與預算 → commit JSON → GitHub Pages 純前端秒載。

- **本機位置**：`C:\Users\施伯承\Desktop\Claude\taiwan-flows`
- **GitHub**：https://github.com/shihpc/taiwan-flows （main 分支）
- **線上**：https://shihpc.github.io/taiwan-flows/
- **規格書**：`taiwan-flows-spec_V1.md`（V1 定稿，部分已被後續需求覆蓋，見下方「規格後的演進」）
- **姊妹專案**：`taiwan-stock-radar`（radar 之後會以本專案輸出的 JSON 為資料源）
- **日期欄語意**：`latest.json`/`meta.json` 的 `date`／`generated_at`（台北 +08:00）／`baseline_date`（投信庫存累計種子日，凍結於 2026-04-30；注意發行張數 `issued_lots` 本身每交易日更新，非凍結）等欄位語意，與跨站產出檔的統一對照，見 postmkt repo 的 `docs/date-semantics.md`。

## 快速接手（最新狀態，2026-07-25 更新）

- **資料現況**：全站對齊 **2026-07-24**（91 個交易日，2026-03-11 起）。每日排程**週一~五 21:19 台北**自動跑（`cron: 19 13 * * 1-5`；2026-07-14 由 21:00 延後——TaiwanStockShareholding 官方 21:00 更新，留緩衝）。
- **雙觸發**：主觸發是 `taiwan-flow-live-v2` 的 Cloudflare Worker「FinMind 哨兵」（台北 19:00–23:00 每 5 分探測法人/持股落地，一落地就 `workflow_dispatch` daily.yml，通常早於 cron）；cron 留作備援，管線冪等、多跑無害。哨兵程式：`taiwan-flow-live-v2/worker/src/index.js` 的 `runSentinel`。
- **另有兩支 workflow**（2026-07-25 新增）：`verify.yml`（23:40 台北，延後獨立驗證收盤價 + 必要時重抓修復）、`parity.yml`（改到 src/index.html 就跑前後端口徑一致性測試，**不需 token**）。

### 2026-07-25 優化批次（全部已驗證）

| 項目 | 內容 |
|------|------|
| **健檢改延後班** | 原本 `run_daily` 在 pipeline 後立刻跑 healthcheck，但它比對的權威源就是 pipeline 剛用過的 `TaiwanStockPrice`，同一次排程內相隔幾秒必然拿到同一份回應 → severity 恆為 ok，**6/26 那類事故完全驗不出來**（6/26 驗得出來是因為事後手動 `--date` 跑）。改由 `src/verify_daily.py` + `verify.yml`（23:40 台北）延後獨立驗證，critical 時直接重抓並重算衍生產出。`run_daily` 當天先把 `status.json.healthcheck.severity` 標 `pending`。 |
| **前後端 parity 測試** | `tests/parity.py` + `tests/extract_js.mjs`：從 index.html 抽出 `aggregateRange`/`jPage*`/`cmpD` 在 node 沙箱跑同一份 daily，與 Python 逐檔逐欄比對（5 視窗全比、零容差）。**抓出 3 類真實漂移並修掉**：①`bias20` 的 MA20 取樣（Python 取「最後 20 個有效收盤」會往停牌前補樣本，JS 取「最近 20 交易日」）②四捨五入（Python `round` 是 banker's、JS `Math.round` 是 half-up → 每視窗數十檔 `*_amt` 差 1 千元；已統一走 `budget.jround`）③排序 tie-break（主鍵同值時 JS 物件把「像整數的鍵」排在字串鍵前，Python 依 meta.stocks 插入序 → **Top30 選出的成員都不同**；兩邊都加次鍵 `code`／類股用 `sector`）。 |
| **token 改 lazy** | `finmind.py` 原本模組層 `TOKEN = _load_token()`，任何 import 鏈碰到就必須有 token → `budget`/`sectors` 明明只讀本地 JSON 也被綁死，無法離線重算或寫測試。改 `get_token()` 延後取。parity workflow 因此不需要 secret。 |
| **daily.yml timeout 30→55 分** | 重試迴圈是 3 次 run_daily（各 3~5 分）＋ 2 次 `sleep 600`，原本 30 分會在第三次嘗試中途被砍；**job 被 timeout 砍時 `Commit & push` 不會執行**，等於前兩次抓好的資料整包丟掉。 |
| **前端首屏** | `sector_latest.json`（~575KB，只有產業別/產業鏈 tab 要用）移出首屏改 lazy；boot 的 5 個串行 await 改 `Promise.all`。首屏 JSON 從 ~1MB 降到 ~150KB。lazy 載入統一走 `lazyJson()`（in-flight 去重 + `FAILED` 標記，避免 `currentSectors` 的 `.then(render)` 在載入失敗時無限重試）。 |
| **自訂區間下載** | `runCustomRange` 原本逐日 `await`（65 天＝65 個序列 RTT、~17MB）→ 改 `fetchDailyMany` 6 條並行（實測 peak in-flight 6 vs 1）。 |
| **sessionStorage** | 原本存**展開後**的物件（698KB/日，65 天要 44MB）→ 遠超 5MB 配額，`setItem` 幾乎必然丟例外被靜靜吃掉，且每次 miss 白做一次 698KB `JSON.stringify`。改存**壓縮原格式**（256KB/日），讀取時才展開；舊格式讀到會自動丟棄重抓。 |
| **argv 清理** | `budget`/`foreign_flows`/`sectors`/`run_daily` 的 `main` 改 `main(argv=None)`，移除 `run_daily` 裡三處 `sys.argv` 竄改；共用的重算流程抽成 `run_daily.rebuild_products()`（`verify_daily` 重抓後也呼叫它）。 |
| **requirements.txt** | 移除開頭的 UTF-8 BOM。 |

**已量測、確認不必優化**：後端運算不是瓶頸——`load_daily` 2.5s、`aggregate` 0.3~0.8s/窗、`budget.main` 4.0s、`sectors.main` 3.6s。`budget` 與 `sectors` 各自重跑一次 load+5 窗（多花約 5s），相對 30 分鐘的 job 無意義，別為此增加耦合。
- **近期重要修正/功能**（細節見下方各段）：
  - `budget.py` 乖離率**除以零防護**（長期未成交股 MA=0 會崩 → 連帶 latest.json/foreign_flows 整串沒更新；已修，是「資料源日期領先資料日」事故的根因）。
  - `daily.yml` push 改 **pull --rebase + 重試 5 次 + fetch-depth:0**（原直接 push 會被前端等新 commit 拒絕）。
  - Excel：**auto-fit 自動欄寬**（消除 #######）、四張並排表字體 11、可選**匯出基準日**、各表標**資料源日期**、ETF市值表移投信/自營佔比。
  - 前端：三大法人卡/台指期卡/ETF概況**可開合**（localStorage）、表格**響應式高度**、各卡/tab**資料源日期徽章**（偵測落後）、右上角改顯示**更新時間**、網頁**「🔄 更新資料」鈕**（PAT 觸發 workflow_dispatch）。
  - **ETF 佔比語意**：市值排行＝持股市值/總市值（外資準、投信/自營對ETF無來源顯「—」、自營佔比欄已移除）；成交金額排行＝成交量參與率(買+賣張)/(2×成交張)。
- **FinMind 現況**：Sponsor **已續約/恢復正常**（2026-06-28，產業鏈等 Sponsor 級資料集可正常抓）。`TaiwanStockPrice` 對**非交易日**（如週末）查詢會回 **HTTP 400 → fm_get 回 None → 報 error**，所以週末手動觸發更新會變紅（屬正常、非 bug）。
- **⚠ 2026-06-26 daily 價格事故（已修）**：6/26 排程跑時 FinMind `TaiwanStockPrice` 尚未更新完成，**922/2687 檔（高價權值股居多，如 2330 存 227 vs 正確 2340）價格被寫成暫定/舊值**，但法人張數正確 → 衍生金額/成交值/市值/Excel/類股資金流全錯。**偵測**：daily.close 與權威 `TaiwanStockPrice` 逐檔比對；**補救**：`python src/pipeline.py --date 2026-06-26` 重抓（現已正確）→ 重跑 budget/sectors。已對全期間掃描：僅 6/26 中招、已修，其餘全相符。
  - **教訓的正確實作（2026-07-25 修正）**：`src/healthcheck.py` 提供純函式 `check()`，但**不能在 run_daily 裡跑**——它的權威源就是 pipeline 剛用過的同一個 API，同一次排程內比對必然相同、severity 恆為 ok（等於沒檢查）。改由 `verify.yml`（23:40 台北）跑 `src/verify_daily.py`：延後數小時後 FinMind 通常已 settle，此時比對才有鑑別力、重抓也才真的修得好，所以**預設就會 `--fix`**（不像原本只印建議），修好後自動 `rebuild_products()` 重算 latest/ranges/foreign/sectors 並 commit。仍 critical 時 job 亮紅通知。前端讀 `status.json.healthcheck.severity`（`pending`＝當天還沒經過獨立驗證／`ok`/`warn`/`critical`）。
- **待辦/暫緩**：
  - 非交易日手動觸發優雅化（`fm_get` 把 400 視為無資料→no_data，避免紅）——使用者請暫緩。
  - ETF 投信/自營持股無絕對來源（已顯「—」）。
  - 讓同事免密碼更新：三方案（Cloudflare Worker 代理／限定版 fine-grained token／GitHub Issue 觸發），尚未實作。

## 環境

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt    # requests, pandas
```
- **`.env`**：放 `FINMIND_TOKEN`（FinMind Sponsor）。**不進 git**，換環境要自己帶。
- 終端 cp950 會把中文顯示成亂碼，但 UTF-8 檔案本身正常；驗證時用 `io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8')`。

## 資料流與指令

```
FinMind(逐檔+期貨) + 證交所BFI82U(上市三大法人) + 櫃買TPEx(上櫃三大法人)
        ↓ src/pipeline.py（單日）/ backfill*.py（回補）
data/daily/*.json、futures/*.json、totals.json、meta.json
        ↓ src/budget.py
data/latest.json（單日）、latest_ranges.json（近5/10/20/65日）
        ↓ index.html（vanilla JS，讀 JSON）
GitHub Pages
```

| 指令 | 說明 |
|------|------|
| `python src/build_meta.py` | 產 meta.json（代號↔名稱、is_etf、issued_lots from baseline） |
| `python src/pipeline.py --date YYYY-MM-DD` | 跑單日：daily + futures + totals |
| `python src/backfill.py --days 65` | 回補逐檔 daily（升序，庫存鏈才正確） |
| `python src/backfill_market.py` | 回補 futures + totals（讀 meta.calendar） |
| `python src/budget.py` | 重算 latest.json + latest_ranges.json |
| `python src/foreign_backfill.py` | 一次性回補上櫃外資逐日（TPEx Daily）→ data/_otc_daily.json（2024→2026-03，已跑完 525 天） |
| `python src/foreign_flows.py` | 重算 foreign_history.json（外資買賣超 tab：FinMind 上市 + _otc_daily/totals 上櫃，月/年聚合） |
| `python src/rebuild_daily.py` | daily schema 變更後重抓歷史（如新增 f/t/d buy/sell 欄；升序保庫存鏈） |
| `python src/sectors.py --build-chain` | (重)抓產業鏈 → `data/industry_chain.json`（snapshot，變動慢、偶爾跑） |
| `python src/sectors.py` | 類股資金流：讀 daily+meta+chain → `sector_latest.json`/`sector_ranges.json` |
| `python src/healthcheck.py [--date D] [--fix]` | daily.close vs 權威源逐檔比對；`--fix` 在 critical 時重抓該日 pipeline |
| `python src/run_daily.py` | 每日排程入口：pipeline + budget + foreign_flows + **sectors** + status.json（daily.yml 用）。**不含健檢**（見上） |
| `python src/verify_daily.py [--date D] [--no-fix]` | **延後驗證班入口**（verify.yml 用）：健檢 → critical 就重抓 → 重算衍生產出 → 寫 status.json.healthcheck |
| `python tests/parity.py [--n 1 5 10 20 65]` | 前端(index.html) ↔ 後端(budget/sectors) 聚合口徑逐檔比對（免 token、免網路） |

每日排程 `.github/workflows/daily.yml`：**13:19 UTC（21:19 台北）**週一~五 + 手動 dispatch。Secret：`FINMIND_TOKEN`。（原 17:30 太早、法人/持股未齊；2026-06 改 21:00；2026-07-14 改 21:19——外資持股官方 21:00 才更新，21:00 整起跑會踩到未更新資料。）

## 資料來源與口徑（重要，踩過的雷）

- **全市場單日查詢用 `start_date`=`end_date`**，不是 `date=`（FinMind `date=` 回 400）。
- **逐檔法人** `TaiwanStockInstitutionalInvestorsBuySell`（長格式，buy/sell 單位**股**÷1000=張）：
  外資=`Foreign_Investor`+`Foreign_Dealer_Self`、投信=`Investment_Trust`、自營=`Dealer_self`+`Dealer_Hedging`(+`Dealer`)。**只存 net（買賣超）**，無逐檔買/賣分項。已驗證與證交所 T86 完全一致。
- **買賣超金額**（逐檔無原生金額欄）= `net張 × 收盤價`（千元），已用規格 sample 與 T86 驗證。
- **股價** `TaiwanStockPrice`：`Trading_Volume`(股÷1000=張)、`Trading_money`(元÷1000=千元)、`spread`=漲跌價，chg_pct=spread/(close−spread)×100。
- **外資持股** `TaiwanStockShareholding`：`ForeignInvestmentShares`(÷1000)、`ForeignInvestmentSharesRatio`(%)。**`NumberOfSharesIssued` 是發行股數**（現值）。
- **投信庫存累計**：`inv(t)=max(0, inv(t-1)+t_net)`，種子來自 `baseline_20260430.json`；≤2026-04-30 為 null。backfill **必須升序**處理。
- **發行張數 issued_lots**：優先用 `TaiwanStockShareholding.NumberOfSharesIssued`（現值，pipeline 每日更新；補上 baseline 後新上市標的如主動式 ETF），baseline 為備援。約 591 檔（多為無外資持股申報的債券 ETF/冷門股）兩者皆無 → issued_lots=None → 市值算不出。
- **市場三大法人卡**（`totals.json`）：**上市**=證交所 `BFI82U`（`https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate=YYYYMMDD&type=day&response=json`），**上櫃**=櫃買 `https://www.tpex.org.tw/www/zh-tw/insti/summary?type=Daily&date=YYYY/MM/DD&response=json`（用「合計」列）。**不用** FinMind 的 `TaiwanStockTotalInstitutionalInvestors`（它在某些日把投信修訂成與官方/自身逐檔不一致的值）。TWSE/TPEx 偶發「線上人數過多」亂 stat 需重試、間隔拉長（6-8s）。TPEx 列名有全形空格前綴、外資列叫「外資及陸資(不含自營商)」。
- **期貨** `TaiwanFuturesInstitutionalInvestors`：外資台指期未平倉淨額 = `long_open_interest_balance_volume − short_...`，金額÷1000。已回補 65 日。
- **代號正規化**：baseline 代號可能去前導 0（`50`↔canonical `0050`），對齊試原碼與 `lstrip('0')`。
- **is_etf**：代號 `00` 開頭。**ETF 債券/平衡型判別**：名稱含「債」**或**代號結尾 **B**(被動債)/**D**(主動債)/**T**(平衡)，其餘股票型。
- **meta.json 不可有 NaN**（瀏覽器 `JSON.parse` 會炸）：build_meta 已過濾 NaN issued_lots 為 null；重建 meta 時**保留既有 calendar**（別清掉）。

## 前端（index.html，單一檔，vanilla JS）

- **6 種模式**：單日 / 5 / 10 / 20 / 65日 / 本週 / 上週 / 上月 / 自訂區間。前 5 個讀 latest/latest_ranges（預算好）；本週/上週/上月/自訂走**瀏覽器端逐日 fetch daily + 聚合**（`runCustomRange`，鏡像 budget.py；口徑一致性由 `tests/parity.py` 自動守門，2026-07-25 起零差異）。
- **首屏只載 4 支小檔**（latest / meta / totals / foreign_history / status，並行）；latest_ranges、sector_latest、sector_ranges、industry_chain 全部 lazy（`lazyJson()`，有 in-flight 去重與失敗標記）。daily 逐日檔走 `fetchDailyMany` 6 條並行 + `state.dailyCache` + sessionStorage（存壓縮原格式）。
- **區間聚合口徑**（規格 4.1）：流量(買賣超)整段加總；存量(持股/比率/乖離)取末日值；漲跌%對 d1 前一交易日；佔成交量=Σnet÷Σvol；乖離=收盤對 MA20。
- **header 由上到下**：標題「外資投信ETF進出」(26px) + 更新時間(10px) → 9 模式鈕 → 資料日/區間 → 三大法人卡 → 台指期卡 → 5 tab。
- **三大法人卡**：上市/上櫃/合計 鈕 + 日/週/月 鈕 + 下拉選期；每法人顯示買/賣/淨。
- **台指期卡**：外資未平倉口數/金額 + 比較日下拉（單日前一日/5/10/20/65日前/本週一/上週五/上月底/自訂首日），顯示比較日 OI 與增減。
- **表格**：固定 ~5 列捲動框（max-height 202px，可拉看 30 筆）、**每欄點標題排序**(▲▼)；代號欄固定 62px、名稱欄 left:62px 對齊（凍結兩欄）；億元統一小數一位含千分位；紅漲綠跌（漲跌% 與買賣超正負）；代號連 Yahoo 股市。
- **投信/外資進出頁**排行鈕：依金額 / 依持股變動率 / 佔成交量（三者皆與買超/賣超連動）。
- 注意：`bindSegs()` 只綁 `.seg[data-seg]`（卡片的日/週/月、上市櫃等 seg 無 data-seg，自行綁 onclick，勿被覆蓋）。

## 外資買賣超 tab（市場別月/年歷史，2026-06 新增）

- **資料**：`data/foreign_history.json`＝`{latest_date, monthly:{"YYYY-MM":{tse:{buy_k,sell_k,net_k},otc:{...}|null}}, daily:{近30交易日}}`（千元）。年總/本週/上週/近5日由前端 `renderForeignFlows()` 聚合，合計＝tse+otc，OTC佔比＝OTC總量÷合計總量。
- **資料源決策（重要）**：**棄用 CMoney 附件**——附件 2026 月值與官方 TWSE 差 ~3 倍且 net 正負相反（附件像更早年份的真實規模；本資料集 2026 市場本身放大 ~2.3×）。改用**官方**：上市＝FinMind `TaiwanStockTotalInstitutionalInvestors`（外資＝Foreign_Investor+Foreign_Dealer_Self，源自證交所；淨額與官方 BFI82U 65 天僅 2 天差、最大 21 億；近 65 天再用 totals.json 官方覆蓋）；上櫃＝TPEx Daily 逐日回補（`_otc_daily.json`，2024-01→2026-03 共 525 天）+ totals.json 近期。**TWSE BFI82U 只支援 type=day**（month 回 HTML、year 回無資料）；**TPEx type=Monthly 忽略 date 只回當月**——所以歷史只能逐日。OTC佔比 13-18%，與附件歷史一致（方法學交叉驗證）。
- **前端**：tab=`foreignflows`，獨立歷史表（凍結「期間」欄 `.flbl` 118px、年列 `.yrow`、分節列 `.srow`），不吃 mode/區間。當月列標「N月（截至 MM/DD）」。Excel 多一張「外資買賣超」工作表（A3 橫向，`xlSheet(wb,name,{a3:true,land:true})`）。

## 類股資金流（2026-06-28 新增，後端＋前端完成）

- **目的**：在現有逐檔法人買賣超上加「分類維度」，看資金流向哪類股。兩種分類法 × 四法人別。
- **前端**：index.html 新增 tab `exch`（產業別資金流）/`chain`（產業鏈資金流）。法人別 seg（預設 total）+ 類股排序表（依買賣超金額、可點欄位排序）+ **點類股展開成分股明細**（`.sectlink` 走 document 級 delegated click 撐過重繪、`#sectDetail` scrollIntoView）。讀 `sector_latest.json`(單日)/`sector_ranges.json`(r5/10/20/65)。chain 頁標多對多/非市佔/不含 ETF + 涵蓋徽章。
- **custom/本週/本月/上週/上月**：`runCustomRange` 算好逐檔 agg 後呼叫 `jPageSectors(agg, chainMap)` 即時建類股 view 存 `state.customSectors`（鏡像後端 `build_view`）；`currentSectors()` 這些模式回 customSectors。需 `ensureChainMap()`（載 `industry_chain.json` 的 `map`）。
- **產業鏈第二層 drill**：chain 頁 產業→**次產業**→成分股。`industry_chain.json` 的 `map[code].p` 存 `(產業,次產業)` 配對（`--build-chain` 產生）；`chainSubLevel()` 用配對把某產業的成分股歸到各次產業、再點次產業列出個股（`.subsectlink` delegated click，`state.sub.chain.opensub`）。展開時上層摘要表壓到 190px 讓深層露出、scrollIntoView。
- 已驗證：exch/chain 排序、兩層 drill、1d/r5/上週、法人別切換、無 console error。例：半導體→IC封測(+116.5億)→旺宏/日月光/南茂/力成。
- **分類法**：`exchange`（交易所產業別，來自 `meta.stocks[code].industry`，**互斥可加總**）/ `chain`（產業鏈 `industry`，來自 `industry_chain.json`，**多對多**）。
- **法人別**：`total`（=f+t+d）/ `foreign` / `trust` / `dealer`。
- **`src/sectors.py`**：**重用 `budget.load_daily`+`budget.aggregate`**（不重抓、不重算流量，口徑同專案：張＋千元）；逐檔歸戶後輸出 `data/sector_latest.json`（單日）、`data/sector_ranges.json`（r5/10/20/65）。結構：`classifications.{exchange|chain}.investors.{total|foreign|trust|dealer}=[{sector,net_amt_k,net_lots,n,n_buy,n_sell}]` + `stocks:[逐檔流量列含 exch/chain 標籤]`（前端點類股→filter stocks 排序個股）。
- **`industry_chain.json`**：`--build-chain` 產出，`map: code→{i:[產業],s:[次產業]}`（2339 檔/47 產業/258KB）。變動慢，不進每日排程、偶爾手動重抓即可。
- **口徑雷（前端徽章務必標）**：
  - chain **多對多**：一檔掛多節點（平均 1.65、最多 21），各節點加總**會重疊、≠大盤、不可讀成市佔**（主題曝險）。**歸戶時要對「產業」去重**（一檔在同產業底下有多個次產業時，勿因 sub_industry 重複計入該產業——否則半導體會從 ~−795 億膨脹成 ~−2700 億）。
  - chain 僅含產業鏈有分類個股（~1940/2328），**不含 ETF/權證**（與 ETF 頁口徑不同）。
- **size**：sector_ranges.json ~2.5MB（逐檔表 ×5 窗）。若 Pages 載入嫌大，可改只存單日逐檔表、區間 drill-down 改前端聚合（鏡像 `runCustomRange`）。

## 規格後的演進（規格書未涵蓋、已實作）

- 新增「外資投信對作」tab（外資投信反向）；同步頁與對作頁加「強度=min(雙方金額)」並依此排序。
- 三大法人卡資料源由 FinMind 改證交所/櫃買，並拆上市/上櫃/合計、顯示買/賣/淨。
- 台指期卡比較基準可選、顯示比較日 OI。
- ETF 頁預設市值排行、股票/債券型按鈕切換、債券判別改 B/D/T+名稱含債。
- 模式新增 本週/上週/上月；issued_lots 改用 Shareholding 現值。

## 檔案結構

```
src/   finmind.py(API client, token lazy) build_meta.py pipeline.py backfill.py
       backfill_market.py futures.py totals.py budget.py sectors.py foreign_flows.py
       healthcheck.py run_daily.py(每日) verify_daily.py(延後驗證)
tests/ parity.py(前後端口徑比對) extract_js.mjs(從 index.html 抽 JS 聚合函式)
data/  daily/YYYYMMDD.json(逐檔20欄) futures/ meta.json totals.json
       latest.json latest_ranges.json sector_latest.json sector_ranges.json
       industry_chain.json foreign_history.json status.json baseline_20260430.json
index.html  taiwan-flows-spec_V1.md
.github/workflows/  daily.yml(21:19 台北) verify.yml(23:40 台北) parity.yml(push 時)
                    canon.yml(push 時，守 CLAUDE.md 頂端 CANON 區塊)
```

**改動 `budget.py`/`sectors.py`/`index.html` 的聚合邏輯時**：這是同一套口徑的兩份實作（單日/近N日走後端預算，自訂區間/本週/上週/上月走前端聚合），**改一邊就要改另一邊**，並跑 `python tests/parity.py --n 1 5 10 20 65` 確認零差異。四捨五入一律用 `budget.jround`（JS `Math.round` 語意），排序一律帶次鍵（`code`／類股用 `sector`），`chg_pct` 的寫法固定 `c/base*100-100`——這三點都是實際踩過的漂移。

daily schema cols：`code,close,chg_pct,vol,amt,t_net,t_amt,f_net,f_amt,d_net,d_amt,t_inv,f_shares,f_pct`（張/千元/%）。

## 2026-06-14 大指令（5 部分）— 全部完成

- **Part 1 ✓**：三大法人卡加「外資佔成交」＝(外資買+賣金額)/(2×市場成交金額)。`totals.json` 每個市場加 `turnover_k`（上市 FMTQIK、上櫃 tradingIndex 月查取當日，皆千元）；`totals.py --backfill-turnover` 回補、`update_total` 每日帶；前端 `computeTot` 加總 turnover、`renderTotCard` 外資列顯示佔比。（6/12 上市 35.3%）
- **Part 2 ✓**：ETF 兩個排行都加 外資/投信/自營佔比＝(買張+賣張)/(2×成交張)；成交金額排行另加 外資買/賣、投信買/賣、自營買/賖（金額）。`budget.py aggregate` 加 f/t/d buy/sell（張）+ 買賣金額（Σ逐日張×當日close，千元）；`page_etf` 加 `share()` + buy/sell；前端 `aggregateRange`/`jPageEtf`/`renderEtf` 同步。
- **Part 4 ✓**：Excel 工作表欄位——外資/投信進出 +持股張/持股市值；外資投信同步 −加總 +投信金額/張+外資金額/張；對作 `OPP_MINI=SYNC_MINI`；ETF市值 −漲跌 +佔比3；大盤三大法人 +外資佔比+買賣分欄；台指期 −金額 +未平倉部位市值。寬表改 A3 橫向、右表起始欄 `RS(cols)=cols.length+2` 動態避重疊。
- **Part 5 ✓**：Excel「ETF市值」與「大盤資金」合併成單一工作表「ETF與大盤」。
- 台指期「未平倉部位市值」＝**名目市值＝口數×加權指數×200**（使用者選定）。TAIEX 加權指數由 FMTQIK 一併存進 `totals.json` rows[d].taiex（`fetch_fmtqik_month`）；`mktval=lots*taiex*200/1e8`（億）。6/12：-65,039 口 → -5,745 億。

### 後續微調（2026-06-14 第二批）
1. 三大法人卡標籤「佔成交」→「外資佔比」。
2. futbar 卡「金額淨額」→「未平倉市值」＝名目市值（lots×taiex×200/1e8）；前端與 Excel 一致。
3. **⚠ 未做**：ETF市值排行加「自營持股市值」——**無自營庫存資料源**（baseline 只有 trust_inv；外資靠 Shareholding、投信靠 baseline 累計，自營兩者皆無）。需接自營庫存來源才能算絕對持股市值。
4. 外資買賣超 tab 比照其他 tab 顯示 futbar（`render()` foreignflows 分支改呼叫 `updateFutbar()`）。
5. 外資買賣超 tab/Excel 版面：近期區塊（上週/本週/近5日**每日5列**）置頂 → 年度累計（改名、新到舊、前端 `state.ffOpen` +/− 摺疊、Excel 月份預設展開）。
6. Excel 全部工作表改 **A4 直向**、`fitToWidth=1`、窄邊界；原並排買賣表改**直向堆疊**（最寬 ≤11 欄，避免橫向溢出/過度縮放）。
7. ETF與大盤工作表 A4 直向、表格改直向堆疊（ETF股票/債券/三大法人/台指期 依序）。

### 第三批 Excel 微調
- 外資買賣超：本週列在上週之前；單位說明併入標題（移除獨立說明行）。
- ETF與大盤「三大法人」期間欄寬 18→26（避免日期被截）。
- 外資/投信進出、同步、對作四張：改回**左右並排（左5右5）**、`xlSheet(...,true)` fitToHeight=1 擠進一張直式 A4、`xlTable(...,fz=8)` 壓字、`xlApplyWidth(ws,W,0.72)` 壓欄。
- **字型慣例**：`xlTable` 有數字格式（c.fmt）的儲存格用 **Arial**（FZN），其餘文字/表頭/標題用 **微軟正黑體**（FZH）；head/sub 也是微軟正黑體。

### 版面精簡微調
- 更新時間併入資料日列：boot 存 `state.updatedTs`、隱藏 `#updated`，`updateDateLabel` 輸出「更新 ts｜資料日/區間｜資料源徽章」同一行（`.updin` 小灰）。
- 收合卡字體縮小（`.csum/.csum .v` 11px、collapsed `.ttl` 12px、padding 3px、line-height 1.2）→ 收合高度 ~23px。
- ETF 概況（整體/股票/債券三卡）可收合：`state.etfStatsOpen`（存 tf_cards.etf），`renderEtf` 加 `#etfStatTgl`，bindSegs 綁定；收合後表格 scrollbox 自動長高。
- 右上狀態邏輯：讀 `status.json.status` — `ok`→「資料已更新 <date>」(綠)、`no_data`→「尚未開盤/非交易日」(黃)、其他→「資料異常」。反映**最近一次 pipeline 執行結果**（run_date 抓不到當日股價即 no_data），非即時盤態。

### ETF 佔比語意（市值排行 vs 成交金額排行）
- **市值排行**的佔比＝**持股市值/總市值**。**外資**＝官方持股比（準）；**投信、自營對 ETF 無可靠絕對來源**（投信 t_inv 缺 ETF baseline 種子而失真、自營無來源）→ `mktcap_row` 的 `t_hold_value_k/t_share/d_share` 一律 None（顯「—」），其他＝市值−外資持股市值。
- **成交金額排行**的佔比＝**成交量參與率**＝(買張+賣張)/(2×成交張)（`turnover_row` 用 `shares()`，三法人皆有值）。兩表語意不同、勿混用。

### 表格響應式高度
- `.scrollbox` 高度改由 `fitScrollbox()` 動態設定＝`視窗高 − 表格top − 14`（下限 160px），依裝置/視窗高決定顯示筆數、超出可下拉。
- 觸發點：render 結尾、renderTotCard/updateFutbar 結尾（卡片開合→表格上移→自動多顯示幾筆）、window resize（120ms debounce）。
- 6 個 tab 都套用（外資買賣超表改 `tablewrap scrollbox`，移除原 max-height:none）。
- 範例（812px 視窗）：兩卡展開 ~7 筆、兩卡收合 ~12 筆。

### 卡片可開合（省空間給 tab）
- 三大法人卡、台指期卡 標題加 ▾/▸ 開合鈕（`state.totOpen/futOpen`，存 `localStorage('tf_cards')`，`loadCardState/saveCardState`）。
- 收合時只剩一行摘要（三大法人：外資淨+佔比；台指期：口數+市值），高度 130→35 / 54→29px，共省 ~120px。
- renderTotCard / updateFutbar 各有收合分支（收合時 updateFutbar 略過比較日 fetch）。

### 各源資料日期徽章（偵測不同步）
- 因各資料源更新時間不一，每卡/tab 顯示自己的「資料源 · MM-DD」徽章；落後於最新源者轉琥珀色標「(落後)」、tooltip 寫來源名。
- 後端：`run_daily.py` 的 `gather_sources()` 把四源最新日寫進 `status.json.sources`：`daily`(meta.calendar 末日)、`totals`(totals.json 末日)、`futures`(futures 最新檔)、`foreign`(foreign_history.latest_date)。
- 前端：`srcDate/newestSrc/srcBadge`（讀 `state.status.sources`，缺時後備用已載入 JSON 的日期）。掛在 三大法人卡(totals)、台指期卡(futures)、daterow(daily，foreignflows 不掛)、外資買賣超 tab 說明列(foreign)。

### Excel 可選基準日 + 各源日期標示
- 模式列加 `#xlsxDate`（type=date，預設 `state.latest.date`、min/max=calendar 範圍）；`xlsxAnchor()` 取值並吸附到 ≤ 該日的最近交易日，`buildExcel` 的 `d2` 改用它（檔名、各表期間都跟著）。
- 各工作表標題改「基準日 d2」，標題下加「資料源：<來源> · <日期>」說明列；`srcAsOf(key,d2)`：daily=d2、totals/futures 取 ≤d2 最近、foreign=歷史最新。ETF與大盤列出三源各自日期。
- 限制：「外資買賣超」工作表是完整歷史（近期段相對最新日），不隨選定的過去 d2 改變。

### 網頁手動更新鍵
- 模式列「🔄 更新資料」鈕：`triggerUpdate()` 直接 POST GitHub Actions `workflows/daily.yml/dispatches`（ref=main）觸發 `daily-flows`。Token 由使用者一次性貼上、存瀏覽器 `localStorage('tf_gh_token')`（不進原始碼/不上傳）；Shift+點 可重設 Token；401/403 自動清除。**注意**：204 只代表 dispatch 已受理，workflow 實際成敗仍要看 Actions 頁（缺 FINMIND_TOKEN secret／太早觸發法人未齊／runner 限流都會讓 run 失敗）。

### 第四批 Excel 微調
- 漲跌幅欄位（mP）改一位小數 `0.0"%"`。
- 四張並排表：**不再用欄寬 ×0.72 壓縮**（會把金額欄壓到比數字窄而出現 #####）；改為數字欄保持足寬（mA/mAm w13、mL w11、外資買賣超 local A w13），只把**間隔欄壓到 1.5** 省空間；整張仍靠 fitToWidth/Height=1 擠進一張 A4。

## 待辦 / 已知限制

- **未做（2026-07-25 評估時明確排除）**：三個 repo（taiwan-flows / taiwan-flow-live-v2 / taiwan-stock-news）各有一份 FinMind client，token 載入、重試、節流各寫一次、行為不一致（例如非交易日 400 只有 taiwan-flows 會炸紅）。要共用需先有套件/vendoring 機制，跨 repo 改動風險高於收益，暫不動。
- **觀察名單**：每個交易日 commit 約 4MB 重算產物（sector_ranges 2.6MB + sector_latest 575KB + latest_ranges 449KB + latest 110KB）。目前靠 git delta 壓縮還撐得住，真要處理最省事的是 `sector_ranges` 不落 git、區間 drill-down 改前端即時聚合（`runCustomRange` 那條路徑已存在且有 parity 守門）。
- 約 591 檔（多為無外資持股申報的債券 ETF + 冷門股）issued_lots=None → 市值缺；要補需接證交所/櫃買 ETF 規模或更完整發行股數來源。
- 逐檔表只有買賣超「淨額」，無買/賖分項（FinMind 有，但 daily schema 未存）；要的話需加欄位 + 重跑回補。
- GitHub Actions 在美國 runner 抓 TWSE/TPEx 偶爾節流；totals.py 已內建重試，若某天漏抓重跑 `backfill_market.py`。
