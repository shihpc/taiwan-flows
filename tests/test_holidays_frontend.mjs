// tests/test_holidays_frontend.mjs —— 前端休市行事曆（2026-09-29 批次二，規格 taiwan-flow-live-v2/docs/holiday-calendar.md §5b）
// 從 index.html 抽出 holParse／holClosed／isTradingDate／lastDueTradingDay／siteStatus 在 node vm 沙箱跑：
// 09-25（週五假日）／09-28（週一假日）／09-26 週六／09-29 平日重演，並驗 fail-open（HOL=null）與改動前只排週末算法逐字相同。
// 用法：node tests/test_holidays_frontend.mjs（pytest 由 tests/test_holidays_frontend.py 代跑）
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import assert from "node:assert/strict";

const ROOT = path.resolve(import.meta.dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const line = (re, w) => { const m = html.match(re); if (!m) throw new Error("找不到 " + w); return m[0]; };
function fn(name) {
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
  throw new Error(`${name} 大括號未配對`);
}
const src = [
  line(/^const fmtDate = .*$/m, "fmtDate"), line(/^const parseDate = .*$/m, "parseDate"),
  "let HOL=null; let NOW=''; const state={};",
  "function taipeiNowStr(){ return NOW; }",
  fn("holParse"), fn("holClosed"), fn("isTradingDate"), fn("lastDueTradingDay"), fn("siteStatus"),
].join("\n");
const sb = { Date, Set, Array, Number, String, Object, console };
vm.createContext(sb);
new vm.Script(src + `
this.setHol = j => { HOL = j == null ? null : holParse(j); return HOL; };
this.setNow = s => { NOW = s; };
this.setState = (latestDate, status) => { state.latest = { date: latestDate }; state.status = status; };
Object.assign(this, { holParse, holClosed, lastDueTradingDay, siteStatus });`).runInContext(sb);

const CAL = JSON.parse(fs.readFileSync(path.join(ROOT, "tests", "fixtures", "twse_holidays_2026.json"), "utf8"));
const pd = s => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
const fd = x => x.getFullYear() + "-" + String(x.getMonth() + 1).padStart(2, "0") + "-" + String(x.getDate()).padStart(2, "0");
// 改動前的 lastDueTradingDay（逐字搬自改動前 index.html）
const oldDue = nowStr => { const dt = pd(nowStr.slice(0, 10)), hour = +nowStr.slice(11, 13), dow = dt.getDay();
  if (dow >= 1 && dow <= 5 && hour >= 20) return fd(dt);
  do { dt.setDate(dt.getDate() - 1); } while (dt.getDay() === 0 || dt.getDay() === 6); return fd(dt); };
let n = 0;
const ok = (name, f) => { f(); n++; console.log("ok  ", name); };
const OK = { status: "ok", date: "2026-09-24" };

ok("解析：schema 必須是整數 1（true 不收）、years 無合法年度＝null", () => {
  assert.equal(sb.holParse({ ...CAL, schema: true }), null);
  assert.equal(sb.holParse({ ...CAL, schema: "1" }), null);
  assert.equal(sb.holParse({ ...CAL, years: [] }), null);
  assert.equal(sb.holParse(null), null);
  assert.notEqual(sb.holParse(CAL), null);
});
ok("fail-open：HOL=null 時 lastDueTradingDay 與改動前逐字相同（8–12 月 × 每小時）", () => {
  sb.setHol(null);
  for (let t = Date.parse("2026-08-01T00:00:00Z"); t <= Date.parse("2026-12-31T00:00:00Z"); t += 864e5) {
    const d = new Date(t).toISOString().slice(0, 10);
    for (let h = 0; h < 24; h++) { const s = `${d} ${String(h).padStart(2, "0")}:00:00`; assert.equal(sb.lastDueTradingDay(s), oldDue(s), s); }
  }
});
ok("lastDueTradingDay：跳過休市日", () => {
  sb.setHol(CAL);
  assert.equal(sb.lastDueTradingDay("2026-09-25 21:00:00"), "2026-09-24");
  assert.equal(sb.lastDueTradingDay("2026-09-28 23:00:00"), "2026-09-24");
  assert.equal(sb.lastDueTradingDay("2026-09-29 10:00:00"), "2026-09-24");
  assert.equal(sb.lastDueTradingDay("2026-09-29 23:00:00"), "2026-09-29");
  sb.setHol({ ...CAL, years: [2025] });                                  // 年度未涵蓋＝只排週末
  assert.equal(sb.lastDueTradingDay("2026-09-29 10:00:00"), "2026-09-28");
});
ok("siteStatus：休市日比照週末＝休市定格；假日後首個交易日照常", () => {
  sb.setHol(CAL);
  sb.setState("2026-09-24", OK);
  for (const s of ["2026-09-25 21:00:00", "2026-09-28 23:00:00", "2026-09-26 10:00:00"]) {
    sb.setNow(s); assert.equal(sb.siteStatus().label, "休市定格", s);
  }
  sb.setNow("2026-09-29 10:00:00"); assert.equal(sb.siteStatus().label, "正常");
  sb.setNow("2026-09-29 23:00:00"); assert.equal(sb.siteStatus().label, "等待資料發布");
  sb.setState("2026-09-24", { status: "missing", expected_date: "2026-09-28" });   // 後端狀態優先，不被行事曆蓋掉
  sb.setNow("2026-09-28 23:00:00"); assert.equal(sb.siteStatus().label, "資料缺漏");
});
ok("siteStatus fail-open：與改動前相同", () => {
  sb.setHol(null); sb.setState("2026-09-24", OK);
  sb.setNow("2026-09-25 21:00:00"); assert.equal(sb.siteStatus().label, "等待資料發布");
  sb.setNow("2026-09-28 23:00:00"); assert.equal(sb.siteStatus().label, "等待資料發布");
  sb.setNow("2026-09-26 10:00:00"); assert.equal(sb.siteStatus().label, "休市定格");
  sb.setNow("2026-09-29 10:00:00"); assert.equal(sb.siteStatus().label, "等待資料發布");
});
console.log(`\n${n} passed`);
