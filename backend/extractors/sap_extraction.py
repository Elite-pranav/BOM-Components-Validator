"""
SAP DATA PDF extractor.

Reads SAP DATA PDF documents using pdfplumber and extracts key-value pairs
from the text content. Lines are parsed using two separator patterns:
  - Asterisk separator:  "Key * Value"
  - Wide whitespace:     "Key    Value"  (2+ spaces)

Extracted data is categorized into:
  - parts:    Known pump component entries (Impeller, Shaft, Diffuser, etc.)
              with parsed material codes and coating flags
  - metadata: All other key-value pairs (order info, specs, etc.)

Output is saved as sap_data.json (categorized) and sap_raw.json (flat KV)
in the processed folder.
"""

import json
import re
from pathlib import Path

import pdfplumber

from backend.extractors.base import BaseExtractor

# Known part/component keys found in SAP DATA documents.
# Values are canonical names (stripped of "Moc"/"MOC" suffixes).
PART_KEYS = {
    "Impeller": "Impeller",
    "Shaft": "Shaft",
    "Top Shaft": "Top Shaft",
    "Int Shaft": "Int Shaft",
    "Diffuser Moc": "Diffuser",
    "Diffuser MOC": "Diffuser",
    "Strainer": "Strainer",
    "Neck Ring": "Neck Ring",
    "Imp Wear Ring": "Imp Wear Ring",
    "Pump Brg Sleeve": "Pump Brg Sleeve",
    "Int Sleeve": "Int Sleeve",
    "Gland Sleeve": "Gland Sleeve",
    "Bearing bush": "Bearing Bush",
    "Bearing Bush": "Bearing Bush",
    "Bearing Bracket": "Bearing Bracket",
    "Suc Bell Mouth": "Suc Bell Mouth",
    "Delivery Bend / Tee": "Delivery Bend / Tee",
    "Motor Stool": "Motor Stool",
    "Column Pipe": "Column Pipe",
}


class SAPExtractor(BaseExtractor):
    """Extracts data from SAP DATA PDF files."""

    def extract(self) -> dict:
        sap_pdf = self._find_sap_pdf()
        if not sap_pdf:
            self.logger.error(f"No SAP DATA PDF found in {self.raw_folder}")
            return {}

        raw_kv = self._extract_key_value_pairs(sap_pdf)
        if not raw_kv:
            self.logger.warning("No key-value pairs extracted from SAP PDF")
            return {}

        self.logger.info(f"Extracted {len(raw_kv)} fields from SAP PDF")

        structured = self._categorize(raw_kv)
        self._save_json(raw_kv, self.processed_folder / "sap_raw.json")
        self._save_json(structured, self.processed_folder / "sap_data.json")

        return structured

    def _find_sap_pdf(self) -> Path | None:
        matches = list(self.raw_folder.glob("*SAP DATA.pdf"))
        return matches[0] if matches else None

    def _extract_key_value_pairs(self, pdf_path: Path) -> dict:
        """Extract key-value pairs from SAP PDF using text parsing."""
        data = {}
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue

                    pair = self._parse_kv_line(line)
                    if pair:
                        data[pair[0]] = pair[1]

        return data

    @staticmethod
    def _parse_kv_line(line: str) -> tuple[str, str] | None:
        """Try to parse a line as a key-value pair.

        Handles two formats:
          - "Key * Value"  (asterisk separator)
          - "Key    Value" (2+ whitespace separator)
        """
        # Pattern 1: asterisk separator
        m = re.match(r"(.+?)\s*\*\s*(.+)", line)
        if m:
            return m.group(1).strip(), m.group(2).strip()

        # Pattern 2: wide whitespace separator (2+ spaces)
        m = re.match(r"(.+?)\s{2,}(.+)$", line)
        if m:
            return m.group(1).strip(), m.group(2).strip()

        return None

    def _categorize(self, raw_kv: dict) -> dict:
        """Split raw KV pairs into parts (components) vs. metadata."""
        parts = {}
        metadata = {}

        for key, value in raw_kv.items():
            canonical = PART_KEYS.get(key)
            if canonical:
                parts[canonical] = {
                    "raw": value,
                    "material": self._extract_material_code(value),
                    "coating": "COATING" in value.upper(),
                }
            else:
                metadata[key] = value

        return {"parts": parts, "metadata": metadata}

    @staticmethod
    def _extract_material_code(value: str) -> str | None:
        """Try to extract a standard material code from a value string."""
        patterns = [
            r"(SS\s?\d{3}\w?)",       # SS304, SS410, SS 316L
            r"(CF\s?\d+M?)",          # CF8M, CF3M
            r"(CA\s?\d+\w*)",         # CA6NM, CA15
            r"(GGG\s?\d+)",           # GGG50
            r"(EN\s?\d+\w*)",         # EN24
            r"\b(CI)\b",             # CI (Cast Iron)
            r"(M\.?S\.?)",           # MS, M.S.
        ]
        upper = value.upper()
        for pat in patterns:
            m = re.search(pat, upper)
            if m:
                return m.group(1).strip()
        return None

    def _save_json(self, data, output_path: Path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)
