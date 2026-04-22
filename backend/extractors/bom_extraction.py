"""
Excel BOM extractor.

Reads PUMP BOM Excel spreadsheets (.XLSX) exported from SAP and extracts
every line item as a clean dict, preserving all columns exactly as they
appear in the sheet.

Design philosophy
-----------------
No categorisation, no part-type inference, no material parsing — those
decisions belong to the downstream Gemini comparison step (Phase 2), which
has full context across all three sources.

The extractor's only responsibilities are:
  1. Find the right .XLSX file
  2. Read every data row (skip the header)
  3. Normalise types (qty as a number, empty strings as None)
  4. Save as bom_data.json

Output shape
------------
A JSON array of objects, one per BOM line item:

[
  {
    "item_number":       "0010",
    "component_number":  "8263538",
    "description":       "STRAINER 2 5638 4900 0501 SS304",
    "quantity":          1,
    "unit":              "PC",
    "text1":             "G.A.DRG.NO.:813351387-40 GA.",
    "text2":             "C.S.DRG.NO.:813351387-40 CS.",
    "sort_string":       "PL BOWL"
  },
  ...
]

Column mapping (0-indexed from the sheet):
  0  Item Number          -> item_number
  1  Component number     -> component_number
  2  Object description   -> description
  3  Comp. Qty (CUn)      -> quantity
  4  Base Unit of Measure -> unit
  5  Item Text Line 1     -> text1
  6  Item text line 2     -> text2
  7  Sort String          -> sort_string
"""

from pathlib import Path

import openpyxl

from backend.extractors.base import BaseExtractor

# Column indices (0-based)
_COL_ITEM_NUMBER      = 0
_COL_COMPONENT_NUMBER = 1
_COL_DESCRIPTION      = 2
_COL_QUANTITY         = 3
_COL_UNIT             = 4
_COL_TEXT1            = 5
_COL_TEXT2            = 6
_COL_SORT_STRING      = 7


class BOMExtractor(BaseExtractor):
    """Extracts all line items from a SAP-exported BOM Excel file."""

    def extract(self) -> list:
        xlsx_file = self._find_xlsx()
        if not xlsx_file:
            self.logger.error(f"No BOM XLSX found in {self.raw_folder}")
            return []

        rows = self._read_excel(xlsx_file)
        if not rows:
            self.logger.warning("No data rows found in BOM Excel")
            return []

        items = [self._parse_row(r) for r in rows]
        self.logger.info(f"Extracted {len(items)} line items from BOM Excel")

        self._save_json(items, self.processed_folder / "bom_data.json")
        return items

    # ── File discovery ─────────────────────────────────────────────────────

    def _find_xlsx(self) -> Path | None:
        for pattern in ("*BOM.XLSX", "*BOM.xlsx"):
            matches = list(self.raw_folder.glob(pattern))
            if matches:
                return matches[0]
        return None

    # ── Reading ────────────────────────────────────────────────────────────

    def _read_excel(self, xlsx_path: Path) -> list[list]:
        """Read all data rows from the first sheet, skipping the header row."""
        wb = openpyxl.load_workbook(
            str(xlsx_path), data_only=True, read_only=True
        )
        ws = wb[wb.sheetnames[0]]

        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                continue  # skip header
            if all(v is None for v in row):
                continue  # skip fully empty rows
            rows.append(list(row))

        wb.close()
        return rows

    # ── Row parsing ────────────────────────────────────────────────────────

    def _parse_row(self, row: list) -> dict:
        """
        Convert a raw Excel row into a clean dict.

        The only transformations applied are:
          - All string values are stripped of surrounding whitespace
          - Empty strings are normalised to None
          - quantity is kept as a number (int or float as Excel stores it)
          - Empty sort_string is stored as None (one row in this pump series
            has no sort string: the Bearing Housing Sub-Assembly)
        """
        def _str(val) -> str | None:
            if val is None:
                return None
            s = str(val).strip()
            return s if s else None

        def _qty(val) -> int | float | None:
            if val is None:
                return None
            # Excel stores quantities as int or float; preserve as-is
            if isinstance(val, (int, float)):
                return val
            try:
                f = float(str(val).strip())
                return int(f) if f == int(f) else f
            except ValueError:
                return None

        return {
            "item_number":       _str(row[_COL_ITEM_NUMBER]),
            "component_number":  _str(row[_COL_COMPONENT_NUMBER]),
            "description":       _str(row[_COL_DESCRIPTION]),
            "quantity":          _qty(row[_COL_QUANTITY]),
            "unit":              _str(row[_COL_UNIT]),
            "text1":             _str(row[_COL_TEXT1]),
            "text2":             _str(row[_COL_TEXT2]),
            "sort_string":       _str(row[_COL_SORT_STRING]),
        }