import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const repo = "/Users/sachinpb/Documents/vp_agent_codex";
const sourcePath = path.join(repo, "traces/omantel_optimized_34_responses_final_20260715.json");
const outputDir = path.join(repo, "outputs/omantel_v2_34_test_cases_20260716");
const outputPath = path.join(outputDir, "Omantel_V2_34_Test_Cases_Inputs_Outputs.xlsx");
const data = JSON.parse(await fs.readFile(sourcePath, "utf8"));

if (!Array.isArray(data) || data.length !== 34) {
  throw new Error(`Expected 34 final responses, found ${Array.isArray(data) ? data.length : "non-array"}`);
}

const workbook = Workbook.create();
const summary = workbook.worksheets.add("Summary");
const cases = workbook.worksheets.add("Test Cases");
const full = workbook.worksheets.add("Full API Responses");

const navy = "#17365D";
const blue = "#2F75B5";
const lightBlue = "#D9EAF7";
const lightGray = "#F2F4F7";
const green = "#E2F0D9";
const amber = "#FFF2CC";
const white = "#FFFFFF";
const dark = "#1F2937";
const border = "#D0D7DE";

for (const sheet of [summary, cases, full]) {
  sheet.showGridLines = false;
}

// Summary sheet
summary.getRange("A1:H2").merge();
summary.getRange("A1").values = [["Omantel Version 2 — 34 Regression Test Cases"]];
summary.getRange("A1:H2").format = {
  fill: navy,
  font: { bold: true, color: white, size: 18 },
  verticalAlignment: "center",
  horizontalAlignment: "left",
};
summary.getRange("A4:B4").values = [["Run detail", "Value"]];
summary.getRange("A5:B9").values = [
  ["Client", "omantel"],
  ["Variant", "Version 2 / optimized"],
  ["Source", "omantel_optimized_34_responses_final_20260715.json"],
  ["Run date", "2026-07-15"],
  ["Workbook prepared", "2026-07-16"],
];
summary.getRange("A4:B4").format = { fill: blue, font: { bold: true, color: white } };
summary.getRange("A5:A9").format = { fill: lightBlue, font: { bold: true, color: dark } };
summary.getRange("A4:B9").format.borders = { preset: "outside", style: "thin", color: border };

summary.getRange("D4:E4").values = [["Result", "Count"]];
summary.getRange("D5:D7").values = [["Total cases"], ["Successful conditions"], ["Clarifications"]];
summary.getRange("E5").formulas = [["=COUNTA('Test Cases'!A5:A38)"]];
summary.getRange("E6").formulas = [["=COUNTIF('Test Cases'!C5:C38,\"Success\")"]];
summary.getRange("E7").formulas = [["=COUNTIF('Test Cases'!C5:C38,\"Clarification\")"]];
summary.getRange("D4:E4").format = { fill: blue, font: { bold: true, color: white } };
summary.getRange("D5:D7").format = { fill: lightBlue, font: { bold: true, color: dark } };
summary.getRange("E5:E7").format = { font: { bold: true, color: navy, size: 14 }, numberFormat: "0" };
summary.getRange("D4:E7").format.borders = { preset: "outside", style: "thin", color: border };

summary.getRange("A12:H12").merge();
summary.getRange("A12").values = [["How to use this workbook"]];
summary.getRange("A12:H12").format = { fill: navy, font: { bold: true, color: white } };
summary.getRange("A13:H16").merge(true);
summary.getRange("A13:H16").values = [
  ["• Test Cases contains the 34 exact inputs, request IDs, rendered conditions or clarification, selected columns, and validation result."],
  ["• Full API Responses contains the complete compact JSON response returned for each case, including raw_text, for auditing."],
  ["• Case 10 is intentionally marked Clarification: the percentage request did not state what the calculated 20% should be compared with or used for."],
  ["• Filters are enabled on the Test Cases and Full API Responses tables."],
];
summary.getRange("A13:H16").format = { fill: lightGray, font: { color: dark }, wrapText: true, verticalAlignment: "center" };
summary.getRange("A13:H16").format.rowHeight = 32;
summary.getRange("A:A").format.columnWidth = 25;
summary.getRange("B:B").format.columnWidth = 60;
summary.getRange("C:C").format.columnWidth = 3;
summary.getRange("D:D").format.columnWidth = 25;
summary.getRange("E:E").format.columnWidth = 15;
summary.getRange("F:H").format.columnWidth = 14;
summary.freezePanes.freezeRows(2);

// Main readable test-case sheet
cases.getRange("A1:L2").merge();
cases.getRange("A1").values = [["Omantel Version 2 — Inputs and Outputs"]];
cases.getRange("A1:L2").format = {
  fill: navy,
  font: { bold: true, color: white, size: 17 },
  verticalAlignment: "center",
};
cases.getRange("A3:L3").merge();
cases.getRange("A3").values = [["Exact final optimized run. Output is the validated PARENT_CONDITION, or the clarification returned by the API."]];
cases.getRange("A3:L3").format = { fill: lightBlue, font: { italic: true, color: dark }, wrapText: true };

const headers = [
  "Case",
  "Input sentence",
  "Status",
  "Request ID",
  "Output / PARENT_CONDITION",
  "Clarification question",
  "Selected columns",
  "Seed",
  "Path",
  "Snapshot",
  "Validation",
  "Warnings",
];
cases.getRange("A4:L4").values = [headers];
cases.getRange("A4:L4").format = {
  fill: blue,
  font: { bold: true, color: white },
  wrapText: true,
  verticalAlignment: "center",
};
cases.getRange("A4:L4").format.rowHeight = 34;

const caseRows = data.map((response, index) => {
  const status = response.ok ? "Success" : (response.needs_clarification ? "Clarification" : "Failed");
  const validation = response.validation == null
    ? "Not run"
    : response.validation.ok
      ? "Passed"
      : `Failed: ${(response.validation.errors || []).join(" | ")}`;
  return [
    index + 1,
    response.sentence ?? "",
    status,
    response.request_id ?? "",
    response.parent_condition ?? response.failure_reason ?? "",
    response.clarification_question ?? "",
    (response.selected_columns || []).join("\n"),
    response.seed ?? "",
    response.path ?? "",
    response.snapshot == null ? "" : response.snapshot,
    validation,
    (response.warnings || []).join("\n"),
  ];
});
cases.getRange("A5:L38").values = caseRows;
cases.getRange("A5:L38").format = {
  font: { color: dark, size: 10 },
  verticalAlignment: "top",
};
cases.getRange("B5:B38").format.wrapText = true;
cases.getRange("E5:G38").format.wrapText = true;
cases.getRange("K5:L38").format.wrapText = true;
cases.getRange("A5:A38").format.horizontalAlignment = "center";
cases.getRange("C5:D38").format.horizontalAlignment = "center";
cases.getRange("J5:K38").format.horizontalAlignment = "center";
cases.getRange("A5:L38").format.rowHeight = 72;
cases.getRange("A5:L38").format.borders = {
  insideHorizontal: { style: "thin", color: border },
};
for (let i = 0; i < data.length; i += 1) {
  const row = 5 + i;
  cases.getRange(`C${row}`).format = {
    fill: data[i].ok ? green : amber,
    font: { bold: true, color: dark },
    horizontalAlignment: "center",
  };
}
cases.getRange("A:A").format.columnWidth = 7;
cases.getRange("B:B").format.columnWidth = 52;
cases.getRange("C:C").format.columnWidth = 15;
cases.getRange("D:D").format.columnWidth = 18;
cases.getRange("E:E").format.columnWidth = 72;
cases.getRange("F:F").format.columnWidth = 52;
cases.getRange("G:G").format.columnWidth = 44;
cases.getRange("H:H").format.columnWidth = 27;
cases.getRange("I:I").format.columnWidth = 34;
cases.getRange("J:J").format.columnWidth = 12;
cases.getRange("K:K").format.columnWidth = 14;
cases.getRange("L:L").format.columnWidth = 45;
cases.freezePanes.freezeRows(4);
cases.freezePanes.freezeColumns(1);
const casesTable = cases.tables.add("A4:L38", true, "OmantelV2TestCases");
casesTable.style = "TableStyleMedium2";
casesTable.showFilterButton = true;

// Complete API output for audit. JSON is compact to stay within Excel's cell limit.
full.getRange("A1:D2").merge();
full.getRange("A1").values = [["Omantel Version 2 — Complete API Responses"]];
full.getRange("A1:D2").format = {
  fill: navy,
  font: { bold: true, color: white, size: 17 },
  verticalAlignment: "center",
};
full.getRange("A3:D3").merge();
full.getRange("A3").values = [["Each JSON cell is the exact final response object from the consolidated Version 2 response log."]];
full.getRange("A3:D3").format = { fill: lightBlue, font: { italic: true, color: dark }, wrapText: true };
full.getRange("A4:D4").values = [["Case", "Request ID", "Input sentence", "Full API response JSON"]];
full.getRange("A4:D4").format = { fill: blue, font: { bold: true, color: white }, wrapText: true };
const fullRows = data.map((response, index) => [
  index + 1,
  response.request_id ?? "",
  response.sentence ?? "",
  JSON.stringify(response),
]);
full.getRange("A5:D38").values = fullRows;
full.getRange("A5:D38").format = { font: { color: dark, size: 9 }, verticalAlignment: "top" };
full.getRange("C5:D38").format.wrapText = true;
full.getRange("A5:B38").format.horizontalAlignment = "center";
full.getRange("A5:D38").format.rowHeight = 72;
full.getRange("A5:D38").format.borders = {
  insideHorizontal: { style: "thin", color: border },
};
full.getRange("A:A").format.columnWidth = 7;
full.getRange("B:B").format.columnWidth = 18;
full.getRange("C:C").format.columnWidth = 52;
full.getRange("D:D").format.columnWidth = 100;
full.freezePanes.freezeRows(4);
full.freezePanes.freezeColumns(2);
const fullTable = full.tables.add("A4:D38", true, "OmantelV2FullResponses");
fullTable.style = "TableStyleMedium2";
fullTable.showFilterButton = true;

await fs.mkdir(outputDir, { recursive: true });

const checks = [];
checks.push((await workbook.inspect({
  kind: "table",
  range: "Summary!A1:H16",
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 12,
  maxChars: 10000,
})).ndjson);
checks.push((await workbook.inspect({
  kind: "table",
  range: "Test Cases!A1:L10",
  include: "values,formulas",
  tableMaxRows: 10,
  tableMaxCols: 12,
  tableMaxCellChars: 250,
  maxChars: 12000,
})).ndjson);
checks.push((await workbook.inspect({
  kind: "table",
  range: "Test Cases!A13:L15",
  include: "values,formulas",
  tableMaxRows: 3,
  tableMaxCols: 12,
  tableMaxCellChars: 500,
  maxChars: 10000,
})).ndjson);
checks.push((await workbook.inspect({
  kind: "table",
  range: "Test Cases!A38:L38",
  include: "values,formulas",
  tableMaxRows: 1,
  tableMaxCols: 12,
  tableMaxCellChars: 500,
  maxChars: 8000,
})).ndjson);
checks.push((await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
})).ndjson);
await fs.writeFile(path.join(outputDir, "verification.txt"), checks.join("\n\n"), "utf8");

for (const [sheetName, range, fileName] of [
  ["Summary", "A1:H16", "preview_summary.png"],
  ["Test Cases", "A1:L12", "preview_test_cases.png"],
  ["Full API Responses", "A1:D8", "preview_full_responses.png"],
]) {
  const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(path.join(outputDir, fileName), new Uint8Array(await preview.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

console.log(JSON.stringify({ outputPath, rowCount: data.length, verificationPath: path.join(outputDir, "verification.txt") }));
