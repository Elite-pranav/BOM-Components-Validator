/**
 * Convert an array of objects to an Excel file and trigger download.
 * Uses the SheetJS (xlsx) library.
 */
import * as XLSX from "xlsx";

export function downloadExcel(data, filename = "export.xlsx", sheetName = "Sheet1") {
  if (!data || data.length === 0) return;

  const ws = XLSX.utils.json_to_sheet(data);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, sheetName);
  XLSX.writeFile(wb, filename);
}
