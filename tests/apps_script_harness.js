// Runs apps_script/Code.gs against a small in-memory imitation of Google Sheets.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const chain = () => new Proxy(function () {}, { get: (t, k) => (k === "build" ? () => ({ rule: true }) : chain()), apply: () => chain() });

class Range {
  constructor(sheet, row, col, rows, cols) { Object.assign(this, { sheet, row, col, rows, cols }); }
  setValues(values) {
    assert.strictEqual(values.length, this.rows);
    values.forEach((line, r) => { assert.strictEqual(line.length, this.cols); line.forEach((v, c) => this.sheet.set(this.row + r, this.col + c, v)); });
    return this;
  }
  setValue(v) { this.sheet.set(this.row, this.col, v); return this; }
  setFormula(f) { this.sheet.set(this.row, this.col, f); return this; }
  getValues() {
    const out = [];
    for (let r = 0; r < this.rows; r++) { const line = []; for (let c = 0; c < this.cols; c++) line.push(this.sheet.get(this.row + r, this.col + c)); out.push(line); }
    return out;
  }
  setDataValidation(rule) { this.sheet.validations.push({ row: this.row, col: this.col, rows: this.rows }); return this; }
}
for (const name of ["setBackground", "setFontWeight", "setFontColor", "setWrap", "setNumberFormat", "setVerticalAlignment", "setFontSize"]) Range.prototype[name] = function () { return this; };

class Sheet {
  constructor(name) { this.name = name; this.cells = new Map(); this.maxRows = 1000; this.validations = []; this.hidden = null; }
  key(r, c) { return `${r}:${c}`; }
  set(r, c, v) { assert(r <= this.maxRows, "wrote past the last row"); this.cells.set(this.key(r, c), v); }
  get(r, c) { return this.cells.has(this.key(r, c)) ? this.cells.get(this.key(r, c)) : ""; }
  getName() { return this.name; }
  setName(n) { this.name = n; return this; }
  getRange(r, c, rows = 1, cols = 1) { assert(r >= 1 && c >= 1 && rows >= 1 && cols >= 1); return new Range(this, r, c, rows, cols); }
  getLastRow() { let last = 0; for (const [k, v] of this.cells) if (v !== "") last = Math.max(last, Number(k.split(":")[0])); return last; }
  getMaxRows() { return this.maxRows; }
  insertRowsAfter(after, count) { this.maxRows += count; return this; }
  hideColumns(start, count) { this.hidden = { start, count }; return this; }
  setFrozenRows() { return this; }
  setColumnWidth() { return this; }
}

function load() {
  const sheets = [new Sheet("Sheet1")];
  const spreadsheet = {
    getId: () => "sheet-id",
    getSheets: () => sheets,
    getSheetByName: (n) => sheets.find((s) => s.getName() === n) || null,
    insertSheet: (n) => { const s = new Sheet(n); sheets.push(s); return s; },
  };
  const properties = { SHEET_SECRET: "secret" };
  const context = {
    console,
    SpreadsheetApp: { getActiveSpreadsheet: () => spreadsheet, openById: () => spreadsheet, newDataValidation: chain, WrapStrategy: { WRAP: 1 } },
    PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => properties[k] || null, setProperty: (k, v) => { properties[k] = v; } }) },
    LockService: { getScriptLock: () => ({ tryLock: () => true, releaseLock: () => {} }) },
    ContentService: { MimeType: { JSON: "json" }, createTextOutput: (text) => ({ text, setMimeType() { return this; } }) },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "apps_script", "Code.gs"), "utf8") + "\nthis.HEADERS_ = HEADERS;", context);
  return { context, spreadsheet };
}

const { context, spreadsheet } = load();
context.setupSheet();
context.setupSheet();
const headers = Array.from(context.HEADERS_);
const names = spreadsheet.getSheets().map((s) => s.getName());
for (const n of ["Start here", "Rate articles", "Summary", "Feeds", "Terms"]) assert(names.includes(n), `missing tab ${n}`);
assert.strictEqual(names.length, 5);

const rate = spreadsheet.getSheetByName("Rate articles");
assert.deepStrictEqual(rate.getRange(1, 1, 1, headers.length).getValues()[0], headers);
assert.deepStrictEqual(rate.hidden, { start: 8, count: headers.length - 7 });

function row(hash, feed, group, terms) {
  const values = Object.fromEntries(headers.map((h) => [h, ""]));
  Object.assign(values, { Title: `Story ${hash}`, Hash: hash, Feed: feed, Group: group, "Matched terms": terms, Useful: "Yes" });
  return headers.map((h) => values[h]);
}
const post = (body) => JSON.parse(context.doPost({ postData: { contents: JSON.stringify(body) } }).text);

assert.strictEqual(post({ secret: "wrong", headers, rows: [row("x", "F", "Sent", "")] }).ok, false);
assert.strictEqual(rate.getLastRow(), 1, "a wrong secret must not add rows");
assert.strictEqual(post({ secret: "secret", headers: ["Other"], rows: [] }).ok, false);

const first = post({ secret: "secret", headers, rows: [row("a", "LNG Prime", "Sent", "|green loan|lng|"), row("b", "ADB", "Rejected by Gemini", "|solar|")] });
assert.strictEqual(first.ok, true);
assert.deepStrictEqual(Array.from(first.received).sort(), ["a", "b"]);
assert.strictEqual(rate.getLastRow(), 3);
assert.strictEqual(rate.get(2, 1), "", "rows must arrive unrated");
assert.strictEqual(rate.validations.length, 2);

rate.set(2, 1, "Yes");
const again = post({ secret: "secret", headers, rows: [row("a", "LNG Prime", "Sent", "|green loan|lng|"), row("c", "LNG Prime", "Sent", "|lng|")] });
assert.deepStrictEqual(Array.from(again.received).sort(), ["a", "c"]);
assert.strictEqual(rate.getLastRow(), 4, "a repeated article must not be added twice");
assert.strictEqual(rate.get(2, 1), "Yes", "existing ratings must be kept");

const firstColumn = (sheet) => sheet.getRange(2, 1, Math.max(sheet.getLastRow() - 1, 1), 1).getValues().map((line) => line[0]);
assert.deepStrictEqual(firstColumn(spreadsheet.getSheetByName("Feeds")).sort(), ["ADB", "LNG Prime"]);
assert.deepStrictEqual(firstColumn(spreadsheet.getSheetByName("Terms")).sort(), ["green loan", "lng"], "only accepted articles count towards terms");

const letter = (name) => { let n = headers.indexOf(name) + 1, out = ""; while (n) { out = String.fromCharCode(65 + ((n - 1) % 26)) + out; n = Math.floor((n - 1) / 26); } return out; };
const feedFormula = spreadsheet.getSheetByName("Feeds").get(2, 2);
assert(feedFormula.includes(`'Rate articles'!$A:$A,"Yes"`) && feedFormula.includes(`'Rate articles'!$${letter("Feed")}:$${letter("Feed")},$A2`), feedFormula);
const termFormula = spreadsheet.getSheetByName("Terms").get(2, 3);
assert(termFormula.includes(`"*|"&$A2&"|*"`) && termFormula.includes(`$${letter("Matched terms")}:$${letter("Matched terms")}`), termFormula);

const many = Array.from({ length: 1200 }, (_, i) => row(`bulk${i}`, "LNG Prime", "Sent", "|lng|"));
assert.strictEqual(post({ secret: "secret", headers, rows: many }).ok, true);
assert.strictEqual(rate.getLastRow(), 1204);
console.log("Apps Script checks passed");
