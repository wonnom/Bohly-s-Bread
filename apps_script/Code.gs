/**
 * Bohly's Bread trial ratings sheet.
 * Receives articles from the news bot and adds them to the sheet for rating.
 * Setup steps are in docs/RATING_SHEET.md.
 */
const RATE_SHEET = "Rate articles";
const VISIBLE_COLUMNS = 7;
const REASONS = ["Off-topic", "Too minor", "Wrong region", "Duplicate", "Old news", "Not Interested"];
const GROUP_SENT = "Sent";
const GROUP_MISSED_CAP = "Accepted, not sent";
const GROUP_REJECTED = "Rejected by Gemini";
const GROUP_CODE = "Removed by code filter";
const HEADER_COLOUR = "#1f3a5f";
const INPUT_COLOUR = "#fff4cc";

const HEADERS = ["Useful", "Reason if not useful", "Title", "Link", "Description", "Source", "Published", "Country", "News type", "Group", "Trial split", "Feed", "Provider", "Status", "Brief", "Brief position", "Shortlist position", "Total score", "Strong terms", "Deal terms", "Sector terms", "Amount", "Source trust", "Matched terms", "Gemini evidence", "Code filter reason", "Hash"];

const START_HERE = [
  "How to rate articles",
  "",
  "1. Open the 'Rate articles' tab. New articles appear at the bottom after each brief.",
  "2. Open each link and judge whether it would be useful in your daily brief.",
  "3. Choose Yes or No under Useful. For No, also choose a reason.",
  "4. Rate whenever you have time. Your ratings save automatically.",
  "",
  "Some articles were deliberately not sent by the bot, so please rate them the same way.",
  "Please do not delete rows or change the column order.",
];

function columnLetter_(index) {
  let number = index;
  let letters = "";
  while (number > 0) {
    letters = String.fromCharCode(65 + ((number - 1) % 26)) + letters;
    number = Math.floor((number - 1) / 26);
  }
  return letters;
}

function columnIndex_(name) {
  return HEADERS.indexOf(name) + 1;
}

function rateRange_(name) {
  const letter = columnLetter_(columnIndex_(name));
  return `'${RATE_SHEET}'!$${letter}:$${letter}`;
}

function countAnswer_(answer, criteria) {
  return criteria.map((item) => `COUNTIFS(${rateRange_("Useful")},"${answer}",${item})`).join("+");
}

function writeRatingCells_(sheet, row, column, criteria) {
  const rated = columnLetter_(column) + row;
  const useful = columnLetter_(column + 1) + row;
  sheet.getRange(row, column).setFormula(`=${countAnswer_("Yes", criteria)}+${countAnswer_("No", criteria)}`);
  sheet.getRange(row, column + 1).setFormula(`=${countAnswer_("Yes", criteria)}`);
  sheet.getRange(row, column + 2).setFormula(`=IF(${rated}=0,"",${useful}/${rated})`).setNumberFormat("0%");
}

function styleHeader_(sheet, row, columns) {
  sheet.getRange(row, 1, 1, columns).setBackground(HEADER_COLOUR).setFontColor("#ffffff").setFontWeight("bold");
}

function writeSection_(sheet, row, title, firstHeader) {
  sheet.getRange(row, 1).setValue(title).setFontWeight("bold");
  sheet.getRange(row + 1, 1, 1, 4).setValues([[firstHeader, "Rated", "Useful", "Useful %"]]);
  styleHeader_(sheet, row + 1, 4);
  return row + 2;
}

function buildSummary_(sheet) {
  const group = rateRange_("Group");
  let row = writeSection_(sheet, 1, "Usefulness by article group", "Group");
  [GROUP_SENT, GROUP_MISSED_CAP, GROUP_REJECTED, GROUP_CODE].forEach((name) => {
    sheet.getRange(row, 1).setValue(name);
    writeRatingCells_(sheet, row, 2, [`${group},$A${row}`]);
    row += 1;
  });

  row = writeSection_(sheet, row + 1, "Sent articles: tune days against check days", "Trial split");
  ["Tune", "Check"].forEach((name) => {
    sheet.getRange(row, 1).setValue(name);
    writeRatingCells_(sheet, row, 2, [`${rateRange_("Trial split")},$A${row},${group},"${GROUP_SENT}"`]);
    row += 1;
  });

  row = writeSection_(sheet, row + 1, "Sent articles: position in the brief", "Position");
  [[1, 4], [5, 8], [9, 12]].forEach(([low, high]) => {
    const position = rateRange_("Brief position");
    sheet.getRange(row, 1).setNumberFormat("@").setValue(`${low} to ${high}`);
    writeRatingCells_(sheet, row, 2, [`${position},">=${low}",${position},"<=${high}"`]);
    row += 1;
  });

  row = writeSection_(sheet, row + 1, "Gemini-accepted articles: ranking score", "Score");
  [[0, 4], [5, 8], [9, 12], [13, 999]].forEach(([low, high]) => {
    const score = rateRange_("Total score");
    const band = `${score},">=${low}",${score},"<=${high}"`;
    sheet.getRange(row, 1).setNumberFormat("@").setValue(high === 999 ? `${low} and above` : `${low} to ${high}`);
    writeRatingCells_(sheet, row, 2, [`${band},${group},"${GROUP_SENT}"`, `${band},${group},"${GROUP_MISSED_CAP}"`]);
    row += 1;
  });

  row += 1;
  sheet.getRange(row, 1).setValue("Reasons articles were not useful").setFontWeight("bold");
  row += 1;
  sheet.getRange(row, 1, 1, 2).setValues([["Reason", "Articles"]]);
  styleHeader_(sheet, row, 2);
  REASONS.forEach((reason) => {
    row += 1;
    sheet.getRange(row, 1).setValue(reason);
    sheet.getRange(row, 2).setFormula(`=COUNTIFS(${rateRange_("Reason if not useful")},$A${row})`);
  });
}

function getOrCreateSheet_(spreadsheet, name) {
  const existing = spreadsheet.getSheetByName(name);
  return { sheet: existing || spreadsheet.insertSheet(name), created: !existing };
}

/** Run this once from the Apps Script editor. It is safe to run again. */
function setupSheet() {
  const properties = PropertiesService.getScriptProperties();
  if (!properties.getProperty("SHEET_SECRET")) {
    throw new Error("Add the SHEET_SECRET script property first. See docs/RATING_SHEET.md.");
  }

  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  properties.setProperty("SPREADSHEET_ID", spreadsheet.getId());

  if (!spreadsheet.getSheetByName("Start here")) {
    const first = spreadsheet.getSheets()[0];
    const intro = first.getName() === "Sheet1" ? first.setName("Start here") : spreadsheet.insertSheet("Start here");
    intro.getRange(1, 1, START_HERE.length, 1).setValues(START_HERE.map((line) => [line]));
    intro.getRange(1, 1).setFontWeight("bold").setFontSize(14);
    intro.setColumnWidth(1, 700);
  }

  const rate = getOrCreateSheet_(spreadsheet, RATE_SHEET);
  if (rate.created) {
    rate.sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);
    styleHeader_(rate.sheet, 1, HEADERS.length);
    rate.sheet.setFrozenRows(1);
    [[1, 80], [2, 140], [3, 380], [4, 180], [5, 420], [6, 130], [7, 100]].forEach(([column, width]) => {
      rate.sheet.setColumnWidth(column, width);
    });
    rate.sheet.hideColumns(VISIBLE_COLUMNS + 1, HEADERS.length - VISIBLE_COLUMNS);
  }

  const summary = getOrCreateSheet_(spreadsheet, "Summary");
  if (summary.created) {
    buildSummary_(summary.sheet);
    summary.sheet.setColumnWidth(1, 300);
  }

  [["Feeds", "Feed"], ["Terms", "Matched term"]].forEach(([name, firstHeader]) => {
    const tab = getOrCreateSheet_(spreadsheet, name);
    if (tab.created) {
      tab.sheet.getRange(1, 1, 1, 4).setValues([[firstHeader, "Rated", "Useful", "Useful %"]]);
      styleHeader_(tab.sheet, 1, 4);
      tab.sheet.setFrozenRows(1);
      tab.sheet.setColumnWidth(1, 260);
    }
  });
}

function firstColumnValues_(sheet) {
  const last = sheet.getLastRow();
  if (last < 2) return new Set();
  return new Set(sheet.getRange(2, 1, last - 1, 1).getValues().map((line) => String(line[0])));
}

function addSummaryRows_(sheet, names, criteriaFor) {
  const known = firstColumnValues_(sheet);
  let row = sheet.getLastRow() + 1;
  names.forEach((name) => {
    if (!name || known.has(name)) return;
    known.add(name);
    sheet.getRange(row, 1).setValue(name);
    writeRatingCells_(sheet, row, 2, criteriaFor(row));
    row += 1;
  });
}

function addRows_(spreadsheet, rows) {
  const rate = spreadsheet.getSheetByName(RATE_SHEET);
  const hashColumn = columnIndex_("Hash");
  const lastRow = rate.getLastRow();
  const known = new Set(
    lastRow < 2 ? [] : rate.getRange(2, hashColumn, lastRow - 1, 1).getValues().map((line) => String(line[0]))
  );

  const valid = rows.filter((row) => Array.isArray(row) && row.length === HEADERS.length && row[hashColumn - 1]);
  const fresh = [];
  valid.forEach((row) => {
    const hash = String(row[hashColumn - 1]);
    if (known.has(hash)) return;
    known.add(hash);
    fresh.push(row.map((value, index) => (index < 2 || value === null ? "" : value)));
  });

  if (fresh.length) {
    const needed = lastRow + fresh.length - rate.getMaxRows();
    if (needed > 0) rate.insertRowsAfter(rate.getMaxRows(), needed);

    const start = lastRow + 1;
    rate.getRange(start, 1, fresh.length, HEADERS.length).setValues(fresh);
    rate.getRange(start, 1, fresh.length, 2).setBackground(INPUT_COLOUR);
    rate.getRange(start, 3, fresh.length, 3).setWrap(true);
    rate.getRange(start, 1, fresh.length, 1).setDataValidation(
      SpreadsheetApp.newDataValidation().requireValueInList(["Yes", "No"], true).setAllowInvalid(false).build()
    );
    rate.getRange(start, 2, fresh.length, 1).setDataValidation(
      SpreadsheetApp.newDataValidation().requireValueInList(REASONS, true).setAllowInvalid(false).build()
    );

    const feedColumn = columnIndex_("Feed") - 1;
    const groupColumn = columnIndex_("Group") - 1;
    const termColumn = columnIndex_("Matched terms") - 1;
    addSummaryRows_(spreadsheet.getSheetByName("Feeds"), fresh.map((row) => String(row[feedColumn])), (row) => [
      `${rateRange_("Feed")},$A${row}`,
    ]);

    const terms = [];
    fresh.forEach((row) => {
      if (row[groupColumn] !== GROUP_SENT && row[groupColumn] !== GROUP_MISSED_CAP) return;
      String(row[termColumn]).split("|").forEach((term) => terms.push(term));
    });
    addSummaryRows_(spreadsheet.getSheetByName("Terms"), terms, (row) => {
      const match = `${rateRange_("Matched terms")},"*|"&$A${row}&"|*"`;
      return [`${match},${rateRange_("Group")},"${GROUP_SENT}"`, `${match},${rateRange_("Group")},"${GROUP_MISSED_CAP}"`];
    });
  }

  return valid.map((row) => String(row[hashColumn - 1]));
}

function jsonResponse_(body) {
  return ContentService.createTextOutput(JSON.stringify(body)).setMimeType(ContentService.MimeType.JSON);
}

/** Receives new articles from the news bot. */
function doPost(event) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(20000)) return jsonResponse_({ ok: false, error: "busy" });

  try {
    const payload = JSON.parse(event.postData.contents);
    const properties = PropertiesService.getScriptProperties();
    const secret = properties.getProperty("SHEET_SECRET");
    if (!secret || payload.secret !== secret) return jsonResponse_({ ok: false, error: "unauthorized" });
    if (JSON.stringify(payload.headers) !== JSON.stringify(HEADERS)) {
      return jsonResponse_({ ok: false, error: "columns_do_not_match" });
    }

    const spreadsheet = SpreadsheetApp.openById(properties.getProperty("SPREADSHEET_ID"));
    return jsonResponse_({ ok: true, received: addRows_(spreadsheet, payload.rows || []) });
  } catch (error) {
    return jsonResponse_({ ok: false, error: "script_error" });
  } finally {
    lock.releaseLock();
  }
}
