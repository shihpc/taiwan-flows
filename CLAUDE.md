# taiwan-flows — 開發接軌文件（給新 Session / 新對話）

三大法人資金流看板：**外資進出 / 投信進出 / 外資投信同步 / 外資投信對作 / ETF市值 / 外資買賣超** 六個分頁。
盤後資料 → GitHub Actions 每日抓取與預算 → commit JSON → GitHub Pages 純前端秒載。

- **本機位置**：`C:\Users\施伯承\Desktop\Claude\taiwan-flows`
- **GitHub**：https://github.com/shihpc/taiwan-flows （main 分支）
- **線上**：https://shihpc.github.io/taiwan-flows/
- **規格書**：`taiwan-flows-spec_V1.md`（V1 定稿，部分已被後續需求覆蓋，見下方「規格後的演進」）
- **姊妹專案**：`taiwan-stock-radar`（radar 之後會以本專案輸出的 JSON 為資料源）

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
| `python src/run_daily.py` | 排程入口：pipeline + budget + foreign_flows + status.json（Actions 用） |

每日排程 `.github/workflows/daily.yml`：**13:00 UTC（21:00 台北）**週一~五 + 手動 dispatch。Secret：`FINMIND_TOKEN`。（原 17:30 太早、法人/持股未齊；2026-06 改 21:00。）

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

- **6 種模式**：單日 / 5 / 10 / 20 / 65日 / 本週 / 上週 / 上月 / 自訂區間。前 5 個讀 latest/latest_ranges（預算好）；本週/上週/上月/自訂走**瀏覽器端逐日 fetch daily + 聚合**（`runCustomRange`，鏡像 budget.py；已驗證自訂近20日 == 預算 r20）。
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

## 規格後的演進（規格書未涵蓋、已實作）

- 新增「外資投信對作」tab（外資投信反向）；同步頁與對作頁加「強度=min(雙方金額)」並依此排序。
- 三大法人卡資料源由 FinMind 改證交所/櫃買，並拆上市/上櫃/合計、顯示買/賣/淨。
- 台指期卡比較基準可選、顯示比較日 OI。
- ETF 頁預設市值排行、股票/債券型按鈕切換、債券判別改 B/D/T+名稱含債。
- 模式新增 本週/上週/上月；issued_lots 改用 Shareholding 現值。

## 檔案結構

```
src/  finmind.py(API client) build_meta.py pipeline.py backfill.py
      backfill_market.py futures.py totals.py budget.py run_daily.py
data/ daily/YYYYMMDD.json(逐檔14欄) futures/ meta.json totals.json
      latest.json latest_ranges.json status.json baseline_20260430.json
index.html  .github/workflows/daily.yml  taiwan-flows-spec_V1.md
```

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

### 網頁手動更新鍵
- 模式列「🔄 更新資料」鈕：`triggerUpdate()` 直接 POST GitHub Actions `workflows/daily.yml/dispatches`（ref=main）觸發 `daily-flows`。Token 由使用者一次性貼上、存瀏覽器 `localStorage('tf_gh_token')`（不進原始碼/不上傳）；Shift+點 可重設 Token；401/403 自動清除。**注意**：204 只代表 dispatch 已受理，workflow 實際成敗仍要看 Actions 頁（缺 FINMIND_TOKEN secret／太早觸發法人未齊／runner 限流都會讓 run 失敗）。

### 第四批 Excel 微調
- 漲跌幅欄位（mP）改一位小數 `0.0"%"`。
- 四張並排表：**不再用欄寬 ×0.72 壓縮**（會把金額欄壓到比數字窄而出現 #####）；改為數字欄保持足寬（mA/mAm w13、mL w11、外資買賣超 local A w13），只把**間隔欄壓到 1.5** 省空間；整張仍靠 fitToWidth/Height=1 擠進一張 A4。

## 待辦 / 已知限制

- 約 591 檔（多為無外資持股申報的債券 ETF + 冷門股）issued_lots=None → 市值缺；要補需接證交所/櫃買 ETF 規模或更完整發行股數來源。
- 逐檔表只有買賣超「淨額」，無買/賖分項（FinMind 有，但 daily schema 未存）；要的話需加欄位 + 重跑回補。
- GitHub Actions 在美國 runner 抓 TWSE/TPEx 偶爾節流；totals.py 已內建重試，若某天漏抓重跑 `backfill_market.py`。
