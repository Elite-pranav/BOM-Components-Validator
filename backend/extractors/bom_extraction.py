"""
Excel BOM extractor.

Reads PUMP BOM Excel spreadsheets (.XLSX) exported from SAP and extracts
each line item into a structured dict with:
  - Part identification (item number, component number, part type)
  - Material and coating info parsed from the description column
  - Quantity, unit of measure, and usage context
  - Category derived from the Sort String column (Bowl, Shaft, ACC, etc.)

Output is saved as bom_excel.json in the processed folder.
"""

import json
import re
from pathlib import Path

import openpyxl

from backend.extractors.base import BaseExtractor

# Column mapping (0-indexed) for the BOM Excel files.
COL_ITEM_NUMBER = 0       # A: Item Number
COL_COMPONENT_NUM = 1     # B: Component number (SAP part number)
COL_DESCRIPTION = 2       # C: Object description
COL_QUANTITY = 3           # D: Comp. Qty
COL_UNIT = 4              # E: Base Unit of Measure
COL_TEXT_1 = 5            # F: Item Text Line 1
COL_TEXT_2 = 6            # G: Item text line 2
COL_SORT_STRING = 7       # H: Sort String

# Abbreviation map: short forms found in description → full part name.
# Used to identify the part type from the description column.
PART_ABBREV = {
    "STRAINER": "Strainer",
    "SUC MTH": "Suction Bell Mouth",
    "DIFF": "Diffuser",
    "TAP CON": "Taper Connecting Piece",
    "NECK RING": "Neck Ring",
    "IMP WEAR RING": "Impeller Wear Ring",
    "IMP N/CAP": "Impeller Nose Cap",
    "IMP DIST SLV": "Impeller Distance Sleeve",
    "IMP": "Impeller",
    "BRG BUSH CARR": "Bearing Bush Carrier",
    "BRG BUSH": "Bearing Bush",
    "BRG HSG": "Bearing Housing Sub-Assembly",
    "I BRG BUSH": "Intermediate Bearing Bush",
    "INT BRG SLV": "Intermediate Bearing Sleeve",
    "INT BRG CARR": "Intermediate Bearing Carrier",
    "SHAFT INT": "Intermediate Shaft",
    "SHAFT RH TOP": "Top Shaft",
    "SHAFT RH": "Pump Shaft",
    "P BRG SLV": "Pump Bearing Sleeve",
    "DIST SLV": "Distance Sleeve",
    "SAND COLL": "Sand Collar",
    "GLD SLV": "Gland Sleeve",
    "GLD SPLIT": "Split Gland",
    "GLD PACK": "Gland Packing",
    "LOCK NUT": "Lock Nut",
    "SLV NUT": "Sleeve Nut",
    "MUF COUP": "Muff Coupling",
    "SPT COLL": "Split Collar",
    "ADJ RING": "Adjusting Ring",
    "WATER DEFL": "Water Deflector",
    "SOLE PLT": "Sole Plate",
    "DBMS": "Delivery Bend & Motor Stool",
    "ALIGN PAD": "Alignment Pad",
    "L STF BOX": "Loose Stuffing Box",
    "ST BOX LOOSE": "Loose Stuffing Box",
    "STF BOX": "Stuffing Box",
    "LOG RING": "Logging Ring",
    "ADPT PLT": "Adapter Plate",
    "R.M.PIPE TAP": "RM Pipe (Taper/Bottom)",
    "R.M.PIPE INT": "RM Pipe (Intermediate)",
    "R.M.PIPE TOP": "RM Pipe (Top)",
    "R.M.PIPE BOT": "RM Pipe (Bottom)",
    "COOLING COIL": "Cooling Coil",
    "RATCHET": "Ratchet",
}

# Sort string to category mapping.
SORT_CATEGORIES = {
    "PL BOWL": "Bowl Assembly",
    "PL SHAFT": "Shaft Assembly",
    "PL RM PIPE": "Rising Main Pipe",
    "PL ACC": "Accessories",
    "PL DB/MS": "Delivery Bend / Motor Stool",
}

# Material patterns commonly found at the end of description strings.
MATERIAL_PATTERNS = [
    r"(SS\d{3}\w?)",
    r"(CF\d+M?\b)",
    r"(CA\d+\w*)",
    r"(GGG\d+)",
    r"(FG\s?\d+)",
    r"(WCB)",
    r"(LTB\d+)",
    r"(CIP\s+Marine)",
    r"(CUTL?\s*RUB(?:BER)?)",
    r"(NITRILE)",
    r"(HTS)",
    r"\b(MS)\b",
]


class BOMExtractor(BaseExtractor):
    """Extracts BOM data from Excel (.XLSX) files."""

    def extract(self) -> list:
        xlsx_file = self._find_xlsx()
        if not xlsx_file:
            self.logger.error(f"No BOM XLSX found in {self.raw_folder}")
            return []

        rows = self._read_excel(xlsx_file)
        if not rows:
            self.logger.warning("No data rows found in BOM Excel")
            return []

        parts = [self._parse_row(r) for r in rows]
        self.logger.info(f"Extracted {len(parts)} line items from BOM Excel")

        self._save_json(parts, self.processed_folder / "bom_excel.json")
        return parts

    def _find_xlsx(self) -> Path | None:
        matches = list(self.raw_folder.glob("*BOM.XLSX"))
        if not matches:
            matches = list(self.raw_folder.glob("*BOM.xlsx"))
        return matches[0] if matches else None

    def _read_excel(self, xlsx_path: Path) -> list[list]:
        """Read all data rows (skip header) from the first sheet."""
        wb = openpyxl.load_workbook(str(xlsx_path), data_only=True, read_only=True)
        ws = wb[wb.sheetnames[0]]

        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:  # skip header
                continue
            # skip fully empty rows
            if all(v is None for v in row):
                continue
            rows.append(list(row))

        wb.close()
        return rows

    def _parse_row(self, row: list) -> dict:
        """Parse a single Excel row into a structured dict."""
        description = str(row[COL_DESCRIPTION] or "").strip()
        text1 = str(row[COL_TEXT_1] or "").strip()
        text2 = str(row[COL_TEXT_2] or "").strip()
        sort_str = str(row[COL_SORT_STRING] or "").strip()

        material = self._extract_material(description)
        has_coating = "+COAT" in description.upper()

        part_type = self._identify_part_type(description)
        category = SORT_CATEGORIES.get(sort_str, sort_str or None)

        # Combine text lines into a single "usage" note
        usage_parts = [t for t in (text1, text2) if t]
        usage = "; ".join(usage_parts) if usage_parts else None

        qty_raw = row[COL_QUANTITY]
        qty = float(qty_raw) if qty_raw is not None else None

        return {
            "item_number": str(row[COL_ITEM_NUMBER] or "").strip(),
            "component_number": str(row[COL_COMPONENT_NUM] or "").strip(),
            "description": description,
            "part_type": part_type,
            "quantity": qty,
            "unit": str(row[COL_UNIT] or "").strip(),
            "material": material,
            "coating": has_coating,
            "category": category,
            "usage": usage,
        }

    @staticmethod
    def _identify_part_type(description: str) -> str | None:
        """Match description to a known part type using abbreviation map.

        Longer abbreviations are checked first so that e.g. "IMP WEAR RING"
        matches before "IMP".
        """
        desc_upper = description.upper()
        # Sort by length descending so longer (more specific) keys match first
        for abbrev in sorted(PART_ABBREV, key=len, reverse=True):
            if desc_upper.startswith(abbrev.upper()):
                return PART_ABBREV[abbrev]
        return None

    @staticmethod
    def _extract_material(description: str) -> str | None:
        """Extract material code from the tail of the description string."""
        upper = description.upper()
        for pat in MATERIAL_PATTERNS:
            m = re.search(pat, upper)
            if m:
                result = m.group(1).strip()
                # Append coating info if present
                if "+COAT" in upper and "COAT" not in result:
                    result += " + COATING"
                return result
        return None

    def _save_json(self, data, output_path: Path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)
