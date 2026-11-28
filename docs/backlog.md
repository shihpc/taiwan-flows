# 待辦與已知事項（backlog）

依鐵律第 5 條：範圍外問題記在這裡，不只寫在 PR 說明。每筆寫明來源與登錄日；處理完就刪掉該筆（歷史看 git）。
「現況未查證」表示該筆抄自既有文件，登錄時沒有重新確認。

## 待處理
- `CLAUDE.md`「跨站契約」表待補：`status.json` 的 `actual_date`（Worker `/status`、看門狗讀）、`data/daily/*.json`／`totals.json`／`futures/`／`latest.json`（postmkt 市場總覽、taiwan-flow-live-v2 `reconcile.yml` 讀）。2026-11-28 瘦身時發現。
- `CLAUDE.md` 內留有本機絕對路徑（含使用者姓名），是否保留待使用者決定。
- 非交易日手動觸發會亮紅（`fm_get` 把 400 視為錯誤）——使用者曾指示暫緩。
- full 版 `sector_latest.json`／`sector_ranges.json` 仍進 git（前端已只讀 lite），要省 git 增量需改為不落 git。
- 約 591 檔 `issued_lots` 為空，市值算不出。
- ETF 的投信／自營持股沒有絕對來源（畫面顯示「—」）。
- 讓同事免密碼觸發更新：三個方案皆未實作。
以上後五筆抄自瘦身前的接手文件，現況未查證。
