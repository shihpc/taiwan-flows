// tests/test_radar.mjs —— 籌碼雷達 tab 的純函式測試（驗收條件正本 docs/radar-tab.md §2 A3）
// 沿用 tests/extract_js.mjs 的做法：從 index.html 按名稱抽宣告、在 node vm 沙箱執行，
// 不改 index.html 結構、不需要 DOM。
//   ・上半：以真實 data/sector_ranges_lite.json 驗 x／y 算式、n>=8 過濾、四象限分類（含 x=0、y=0 邊界）
//   ・下半：以合成 diag 驗排序、產業篩選、缺 hdw 的檔排除
// 用法：node tests/test_radar.mjs（離線、免 token；任一斷言失敗 exit 1）
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import assert from "node:assert/strict";

const ROOT = path.resolve(import.meta.dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");

function pickConst(name) {
  const m = html.match(new RegExp(`^const ${name}\\s*=.*$`, "m"));
  if (!m) throw new Error(`index.html 找不到 const ${name}`);
  return m[0];
}
function pickFunc(name) {
  const start = html.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`index.html 找不到 function ${name}`);
  const open = html.indexOf("{", start);
  let depth = 0, inStr = null;
  for (let i = open; i < html.length; i++) {
    const c = html[i];
    if (inStr) { if (c === "\\") { i++; continue; } if (c === inStr) inStr = null; }
    else if (c === '"' || c === "'" || c === "`") inStr = c;
    else if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) return html.slice(start, i + 1); }
  }
  throw new Error(`function ${name} 大括號未配對`);
}

const src = [
  ...["RADAR_MIN_N", "radarSq"].map(pickConst),
  ...["radarQuad", "radarPoints", "radarHolders"].map(pickFunc),
].join("\n");
const sb = { Math, Object, Array, Set, String, isFinite, console };
vm.createContext(sb);
new vm.Script(src + "\nthis.radarQuad=radarQuad;this.radarPoints=radarPoints;this.radarHolders=radarHolders;this.RADAR_MIN_N=RADAR_MIN_N;").runInContext(sb);
// 沙箱回傳的陣列／物件屬於另一個 realm，deepStrictEqual 會因原型不同而判不等 → 以 JSON 往返轉回本 realm
// （NaN／undefined 不會出現在回傳值裡：radarHolders 已排除非有限數的 hdw）
for (const k of ["radarPoints", "radarHolders"]) { const f = sb[k]; sb[k] = (...a) => JSON.parse(JSON.stringify(f(...a))); }

let n = 0;
const ok = (name, fn) => { fn(); n++; console.log("ok  ", name); };
const close = (a, b) => Math.abs(a - b) < 1e-9;

// ── 1. 四象限邊界 ────────────────────────────────────────────────
ok("象限：x=0、y=0 歸非負側", () => {
  assert.equal(sb.radarQuad(0, 0), "acc_in");
  assert.equal(sb.radarQuad(0, -1e-9), "slow_in");
  assert.equal(sb.radarQuad(-1e-9, 0), "slow_out");
  assert.equal(sb.radarQuad(-1, -1), "acc_out");
  assert.equal(sb.radarQuad(3, 2), "acc_in");
  assert.equal(sb.radarQuad(3, -2), "slow_in");
  assert.equal(sb.radarQuad(-3, 2), "slow_out");
});
ok("RADAR_MIN_N 為 8", () => assert.equal(sb.RADAR_MIN_N, 8));

// ── 2. 真實 sector_ranges_lite.json ─────────────────────────────
const lite = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "sector_ranges_lite.json"), "utf8"));
const r5 = lite.windows.r5, r20 = lite.windows.r20;
for (const cls of ["exchange", "chain"]) for (const inv of ["total", "foreign", "trust", "dealer"]) {
  ok(`真實資料 ${cls}/${inv}：x／y 算式、n>=8 過濾、象限`, () => {
    const pts = sb.radarPoints(r5, r20, cls, inv);
    const L5 = r5.classifications[cls].investors[inv], L20 = r20.classifications[cls].investors[inv];
    const m5 = Object.fromEntries(L5.map(r => [r.sector, r]));
    const expect = L20.filter(r => m5[r.sector] && r.n >= 8).map(r => r.sector).sort();
    assert.deepEqual(pts.map(p => p.sector).sort(), expect, "類股集合＝兩窗都有且 r20.n>=8");
    assert.ok(pts.length > 0);
    for (const p of pts) {
      const b = L20.find(r => r.sector === p.sector), a = m5[p.sector];
      const x = b.net_amt_k / r20.trading_days / 1e5;
      const y = a.net_amt_k / r5.trading_days / 1e5 - x;
      assert.ok(close(p.x, x) && close(p.y, y), `${p.sector} x/y`);
      assert.ok(close(p.v20, b.net_amt_k / 1e5) && close(p.v5, a.net_amt_k / 1e5));
      assert.equal(p.n, b.n);
      assert.ok(p.n >= 8);
      assert.equal(p.q, x >= 0 ? (y >= 0 ? "acc_in" : "slow_in") : (y < 0 ? "acc_out" : "slow_out"));
    }
    for (let i = 1; i < pts.length; i++) assert.ok(pts[i - 1].v20 >= pts[i].v20, "預設依近 20 日淨額由大到小");
  });
}
ok("交易日數取自窗的 trading_days（不寫死 5／20）", () => {
  const w5 = { trading_days: 4, classifications: { exchange: { investors: { total: [{ sector: "A", net_amt_k: 4e5, n: 9 }] } } } };
  const w20 = { trading_days: 19, classifications: { exchange: { investors: { total: [{ sector: "A", net_amt_k: 19e5, n: 9 }] } } } };
  const [p] = sb.radarPoints(w5, w20, "exchange", "total");
  assert.ok(close(p.x, 1) && close(p.y, 0)); assert.equal(p.q, "acc_in");   // y=0 邊界
});
ok("合成：n=7 排除、n=8 保留、只在一窗的排除、x=0 邊界", () => {
  const mk = (td, rows) => ({ trading_days: td, classifications: { chain: { investors: { trust: rows } } } });
  const w5 = mk(5, [{ sector: "A", net_amt_k: -5e5, n: 8 }, { sector: "B", net_amt_k: 1, n: 7 }, { sector: "C", net_amt_k: 0, n: 30 }, { sector: "Z", net_amt_k: 1, n: 30 }]);
  const w20 = mk(20, [{ sector: "A", net_amt_k: 0, n: 8 }, { sector: "B", net_amt_k: 1, n: 7 }, { sector: "C", net_amt_k: -20e5, n: 30 }, { sector: "D", net_amt_k: 1, n: 30 }]);
  const pts = sb.radarPoints(w5, w20, "chain", "trust");
  assert.deepEqual(pts.map(p => p.sector), ["A", "C"]);
  const A = pts[0], C = pts[1];
  assert.equal(A.x, 0); assert.ok(close(A.y, -1)); assert.equal(A.q, "slow_in");   // x=0 → 流入側
  assert.ok(close(C.x, -1) && close(C.y, 1)); assert.equal(C.q, "slow_out");
});

// ── 3. 合成 diag ────────────────────────────────────────────────
const diag = { date: "2026-09-24", stocks: {
  "1111": { n: "甲", ind: "半導體業", hd: [50.5, 50.0], hdw: 0.5, hdd: "2026-09-18", f5: 10, t5: -3 },
  "2222": { n: "乙", ind: "半導體業", hd: [40, 41], hdw: -1, hdd: "2026-09-18", f5: null, t5: 0 },
  "3333": { n: "丙", ind: "航運業", hd: [30, 28], hdw: 2, hdd: "2026-09-18", f5: 1, t5: 1 },
  "0444": { n: "丁", ind: "航運業", hd: [20, 19.5], hdw: 0.5, hdd: "2026-09-11", f5: 1, t5: 1 },   // 與 1111 同值 → 代號次鍵
  "5555": { n: "戊", ind: "航運業", hd: [20, null], hdw: null, hdd: "2026-09-18" },            // 缺 hdw → 排除
  "6666": { n: "己", ind: "食品工業", hd: [10, 10] },                                             // 無 hdw 欄 → 排除
  "7777": { n: "庚", ind: "食品工業", hd: [10, 12], hdw: -2, hdd: "2026-09-18" },
  "8888": { n: "辛", ind: "食品工業", hd: [10, 10], hdw: 0, hdd: "2026-09-18" },                  // 0 → 不進增減兩表但計入涵蓋
  "9999": { n: "壬", ind: "食品工業", hdw: NaN },                                                 // NaN → 排除
} };
ok("diag：缺 hdw 排除、涵蓋數、集保日取 max、stale 計數", () => {
  const H = sb.radarHolders(diag, "all");
  assert.equal(H.cover, 6);
  assert.equal(H.hdd, "2026-09-18");
  assert.equal(H.stale, 1);
  const codes = [...H.up, ...H.down].map(r => r.code);
  for (const c of ["5555", "6666", "9999", "8888"]) assert.ok(!codes.includes(c), c);
  assert.deepEqual([...H.inds], ["半導體業", "航運業", "食品工業"].sort((a, b) => a.localeCompare(b, "zh-Hant")));
});
ok("diag：增加依 hdw 由大到小、同值代號次鍵；減少依 hdw 由小到大", () => {
  const H = sb.radarHolders(diag, "all");
  assert.deepEqual(H.up.map(r => r.code), ["3333", "0444", "1111"]);
  assert.deepEqual(H.down.map(r => r.code), ["7777", "2222"]);
  assert.equal(H.up[0].hd, 30, "hd 取第 0 個（postmkt 定義 [最新週, 前一週]）");
  assert.equal(H.down[1].f5, null);
});
ok("diag：產業篩選（涵蓋數不受篩選影響）", () => {
  const H = sb.radarHolders(diag, "航運業");
  assert.deepEqual(H.up.map(r => r.code), ["3333", "0444"]);
  assert.deepEqual(H.down, []);
  assert.equal(H.filtered, 2); assert.equal(H.cover, 6);
  assert.equal(sb.radarHolders(diag, "不存在").filtered, 0);
});
ok("diag：各表最多 20 檔", () => {
  const st = {};
  for (let i = 0; i < 50; i++) st[String(1000 + i)] = { n: "x", ind: "A", hd: [1, 1], hdw: (i - 25) / 10, hdd: "2026-09-18" };
  const H = sb.radarHolders({ stocks: st }, "all");
  assert.equal(H.up.length, 20); assert.equal(H.down.length, 20);
  assert.equal(H.up[0].hdw, 2.4); assert.equal(H.down[0].hdw, -2.5);
});
ok("diag：null／無 stocks 不炸", () => {
  assert.equal(sb.radarHolders(null, "all").cover, 0);
  assert.equal(sb.radarHolders({}, "all").hdd, null);
});
console.log(`\n${n} 項全部通過`);
