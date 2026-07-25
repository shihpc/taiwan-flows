// tests/extract_js.mjs
// 從 index.html 抽出「鏡像 budget.py / sectors.py 的前端聚合函式」，在 node 裡跑同一份
// 本地 daily 資料，把結果寫成 JSON 供 tests/parity.py 與 Python 端逐檔比對。
//
// 為什麼要這樣做：index.html 是單一檔 vanilla JS，aggregateRange / jPage* 是
// budget.aggregate / page_* 的逐行移植（自訂區間與本週/上週/上月走前端聚合）。
// 兩份實作沒有守門機制就會靜默漂移（2026-07 已發生一次：bias20 的 MA20 取樣不同）。
// 這裡用「按名稱抽宣告 + vm 沙箱執行」的方式，不需要改動 index.html 的結構。
//
// 用法：
//   node tests/extract_js.mjs <out.json> <n>      # n = 視窗交易日數（1/5/10/20/65）

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const ROOT = path.resolve(import.meta.dirname, "..");
const OUT = process.argv[2];
const N = parseInt(process.argv[3] || "20", 10);
if (!OUT) {
  console.error("用法：node tests/extract_js.mjs <out.json> <n>");
  process.exit(2);
}

// ── 1. 從 index.html 抽宣告 ──────────────────────────────────────
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");

/** 抽單行的 const 宣告（arrow / 物件字面值），以行首 `const NAME` 定位。 */
function pickConst(name) {
  const re = new RegExp(`^const ${name}\\s*=.*$`, "m");
  const m = html.match(re);
  if (!m) throw new Error(`index.html 找不到 const ${name}`);
  return m[0];
}

/** 抽 function 宣告：從 `function NAME(` 起，靠大括號配對找到結尾。 */
function pickFunc(name) {
  const start = html.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`index.html 找不到 function ${name}`);
  const open = html.indexOf("{", start);
  let depth = 0, inStr = null, prev = "";
  for (let i = open; i < html.length; i++) {
    const c = html[i];
    if (inStr) {
      // 字串/樣板字面值內的大括號不算層級；反斜線轉義跳過下一字元
      if (c === "\\") { i++; continue; }
      if (c === inStr) inStr = null;
    } else if (c === '"' || c === "'" || c === "`") {
      inStr = c;
    } else if (c === "{") {
      depth++;
    } else if (c === "}") {
      depth--;
      if (depth === 0) return html.slice(start, i + 1);
    }
    prev = c;
  }
  throw new Error(`function ${name} 大括號未配對`);
}

const CONSTS = ["round", "INV_FIELD", "EXCH_ALIAS", "exchName", "jr", "cmpD"];
const FUNCS = ["aggregateRange", "jInstRow", "jPageInst", "jPageEtf",
               "jPageSync", "jPageOppose", "jPageSectors"];

const src = [...CONSTS.map(pickConst), ...FUNCS.map(pickFunc)].join("\n\n");

// ── 2. 沙箱執行（不需要 DOM：這些函式都是純運算）─────────────────
const sandbox = { Math, Object, JSON, Array, isNaN, console };
vm.createContext(sandbox);
new vm.Script(src).runInContext(sandbox);

// ── 3. 準備與 Python 端相同的輸入 ───────────────────────────────
const DATA = path.join(ROOT, "data");
const meta = JSON.parse(fs.readFileSync(path.join(DATA, "meta.json"), "utf8"));

// dates 取自 data/daily 實際檔案（與 budget.load_daily 同源，非 meta.calendar）
const dates = fs.readdirSync(path.join(DATA, "daily"))
  .filter(f => f.endsWith(".json"))
  .map(f => `${f.slice(0, 4)}-${f.slice(4, 6)}-${f.slice(6, 8)}`)
  .sort();

// 與前端 fetchDaily 一樣：壓縮的 {cols,rows} → {code: rowObject}
function loadDaily(d) {
  const doc = JSON.parse(fs.readFileSync(path.join(DATA, "daily", d.replaceAll("-", "") + ".json"), "utf8"));
  const map = {};
  for (const r of doc.rows) {
    const o = {};
    doc.cols.forEach((c, i) => (o[c] = r[i]));
    map[r[0]] = o;
  }
  return map;
}

const docs = {};
for (const d of dates) docs[d] = loadDaily(d);

// 視窗切法必須與 budget.aggregate 完全一致
const win = N <= dates.length ? dates.slice(-N) : dates;
const i1 = dates.indexOf(win[0]);
const prevDate = i1 > 0 ? dates[i1 - 1] : null;
const ma20dates = dates.slice(-20);   // 對齊 budget.MA_PERIOD

// ── 4. 跑前端實作 ───────────────────────────────────────────────
const agg = sandbox.aggregateRange(win, docs, meta, prevDate, ma20dates);

const chainSnap = JSON.parse(fs.readFileSync(path.join(DATA, "industry_chain.json"), "utf8"));

const out = {
  n: N,
  window: { start: win[0], end: win[win.length - 1], trading_days: win.length },
  agg,
  pages: {
    etf: sandbox.jPageEtf(agg),
    trust: sandbox.jPageInst(agg, "t"),
    foreign: sandbox.jPageInst(agg, "f"),
    sync: sandbox.jPageSync(agg),
    oppose: sandbox.jPageOppose(agg),
  },
  sectors: sandbox.jPageSectors(agg, chainSnap.map),
};

fs.writeFileSync(OUT, JSON.stringify(out));
console.error(`[extract_js] n=${N} 視窗 ${win[0]}~${win[win.length - 1]}，agg ${Object.keys(agg).length} 檔 → ${OUT}`);
