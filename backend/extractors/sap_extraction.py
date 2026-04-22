"""
SAP DATA PDF extractor.

Extracts ALL key-value pairs from an SAP DATA PDF without any hardcoded
schema, part list, or categorisation. The downstream Gemini comparison
step (Phase 2) decides which keys are part materials vs. config values.

Output shape (sap_data.json)
----------------------------
A JSON object with two fields, consistent with the list-based shape of
cs_bom.json and bom_data.json so the frontend handles all three sources
the same way:

{
  "entries": [
    {"key": "Region",    "value": "India"},
    {"key": "Impeller",  "value": "CA6NM + COATING"},
    ...
  ],
  "design_text": "MOC of all stage Diffuser - SG IRON / GGG50 + Coating\n..."
}

Keys are cleaned (trailing SAP asterisk stripped). Empty string values are
normalised to None. Duplicate keys (e.g. "List of Accessories" appearing
twice) produce two separate entries in the list — order is preserved.
"""

import re
from pathlib import Path

import pdfplumber

from backend.extractors.base import BaseExtractor

_HEADER_KEYS = {"Characteristics", "Value"}
_MULTILINE_KEY_RE = re.compile(r".+\n.+")


class SAPExtractor(BaseExtractor):
    """Extracts all data from SAP DATA PDF files."""

    def extract(self) -> dict:
        sap_pdf = self._find_sap_pdf()
        if not sap_pdf:
            self.logger.error(f"No SAP DATA PDF found in {self.raw_folder}")
            return {}

        entries, design_text = self._extract_all(sap_pdf)

        if not entries:
            self.logger.warning("No entries extracted from SAP PDF")
            return {}

        self.logger.info(f"Extracted {len(entries)} entries from SAP PDF")

        result = {"entries": entries, "design_text": design_text}
        self._save_json(result, self.processed_folder / "sap_data.json")
        return result

    # ── File discovery ─────────────────────────────────────────────────────

    def _find_sap_pdf(self) -> Path | None:
        for pattern in ("*SAP DATA.pdf", "*SAP_DATA.pdf", "*SAP*.pdf"):
            matches = list(self.raw_folder.glob(pattern))
            if matches:
                return matches[0]
        return None

    # ── Extraction ─────────────────────────────────────────────────────────

    def _extract_all(self, pdf_path: Path) -> tuple[list[dict], str | None]:
        """
        Extract all key-value pairs from the SAP PDF via pdfplumber table
        detection. Returns an ordered list of {"key", "value"} dicts and
        the free-text Design Text block if present.
        """
        entries: list[dict] = []
        design_text: str | None = None

        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    for row in table:
                        if not row or len(row) < 2:
                            continue

                        key_raw: str = row[0] or ""
                        val_raw = row[1]

                        # Multi-line key = structural block (address, header, etc.)
                        # Extract Design Text from it if present, then skip.
                        if _MULTILINE_KEY_RE.match(key_raw):
                            if val_raw is None:
                                dt = _extract_design_text(key_raw)
                                if dt and design_text is None:
                                    design_text = dt
                            continue

                        if val_raw is None:
                            continue

                        key = _clean_key(key_raw)
                        if not key or key in _HEADER_KEYS:
                            continue

                        # Normalise empty strings to None
                        value = str(val_raw).strip()
                        value = value if value else None

                        entries.append({"key": key, "value": value})

        return entries, design_text


# ── Module helpers ─────────────────────────────────────────────────────────

def _clean_key(raw: str) -> str | None:
    """Strip surrounding whitespace and trailing SAP asterisk from a key."""
    key = raw.strip()
    key = re.sub(r"\s*\*\s*$", "", key).strip()
    return key or None


def _extract_design_text(block: str) -> str | None:
    """Pull the Design Text content from a merged header cell block."""
    m = re.search(
        r"Design Text\s*:\s*\n(.+?)(?:\nConfiguration Data|$)",
        block,
        re.DOTALL,
    )
    return m.group(1).strip() if m else None