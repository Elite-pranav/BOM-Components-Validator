"""
Cross-Section (CS) PDF extractor.

Reads engineering cross-section PDF drawings, renders them to images,
crops the BOM table region, and sends it to Google Gemini's vision API
for AI-powered text extraction. The extracted parts list is saved as
cs_bom.json in the processed folder.

Pipeline:
  1. Render PDF page to high-DPI PNG
  2. Crop and rotate the table region (bottom-right of the drawing)
  3. Send cropped image to Gemini with a structured prompt
  4. Parse the JSON response into a list of part dicts
"""

import json
from pathlib import Path

import cv2
import fitz
import google.generativeai as genai
import PIL.Image

from backend import config
from backend.extractors.base import BaseExtractor

CS_PROMPT = """
You are a precision technical document parser reading a BOM (Bill of Materials)
table from a pump engineering cross-section drawing.

COLUMN ORDER left to right:
  REF  |  DESCRIPTION  |  QTY  |  MATERIAL

═══ CRITICAL RULES — follow every one exactly ═══

1. EXTRACT EVERY ROW WITHOUT EXCEPTION
    Count carefully top to bottom and
   do not skip any row. Every row in the table must appear in your output.

2. SPANNING / MERGED MATERIAL CELLS  ← most important rule
   The MATERIAL column uses merged cells — one material value is printed
   once and covers multiple consecutive rows. The rows below it have visually
   empty material cells but they share the same material.

   HOW TO HANDLE: Read top to bottom. When you see a blank material cell,
   assign it the same material as the most recent non-blank material cell
   above it. Keep that material until you see another printed material value.

   KNOWN SPANNING GROUPS IN THIS TABLE (use as a reference):
     - M.S. covers: FOUNDATION BOLTS (4640) and ERECTION PACKERS (4600)
     - NITRILE RUBBER covers: 4250-4, 4250-3, 4250-1, 4250
     - CHAMPION AF 120 covers: 4080-2 and 4080-1
     - ASTM A276 GR SS410 covers: 3260, 3250, 3210, 3032, 3031
     - IS:1570 GR.40C8 covers: 3011 and 2883
     - ASTM A276 GR SS410 covers: 2834, 2832-2, 2832-1, 2832
     - CUTLESS RUBBER+SS410 SHELL covers: 2830-1 and 2830
     - CUTLESS RUBBER+SS410 SHELL covers: 2801-1 and 2801
     - CI IS 210 GR FG260 covers: 2401, 2318, 2311
     - ASTM A276 GR SS410 covers: 2060 and 2050
     - ASTM A276 GR SS410T covers: 1805-2, 1805-1, 1805, 1803, 1801
     - M.S. covers: 1161, 1151-1, 1151, 1041

3. NULL ONLY WHEN TRULY BLANK
   Only output null for material if you have confirmed there is genuinely
   no material printed for that row's group anywhere in the table.

4. EXACT TEXT
   Copy all text exactly as printed. Include prefixes (ASTM, CI, IS, M.S.)
   and suffixes (+ COATING, GR SS410, HARD CHROME PLT). Do not abbreviate.

5. QTY FORMAT
   Use a number when possible. Use the string "AS REQD" when printed.
   Never output null for qty.

6. SKIP HEADER ROW
   The last row contains REF. / DESCRIPTION / QTY. / MATERIAL. — skip it.

7. SYMBOL ROWS
   Some rows have a triangle/revision symbol in the left margin.
   Include those rows normally — the symbol is not part of REF or DESCRIPTION.

Return ONLY a raw JSON array — no markdown fences, no explanation, nothing else.
Each element: {"ref": "...", "description": "...", "qty": ..., "material": ...}
"""


class CSExtractor(BaseExtractor):
    """Extracts BOM from Cross-Section (CS) PDF drawings."""

    def extract(self) -> list:
        cs_pdf = self._find_cs_pdf()
        if not cs_pdf:
            self.logger.error(f"No CS PDF found in {self.raw_folder}")
            return []

        rendered = self._render_pdf(cs_pdf)
        cropped = self._crop_table(rendered)
        bom_data = self._extract_with_ai(cropped)

        if bom_data is not None:
            self._save_json(bom_data, self.processed_folder / "cs_bom.json")
            self.logger.info(f"Extracted {len(bom_data)} parts from CS drawing")
            return bom_data

        return []

    def _find_cs_pdf(self) -> Path | None:
        matches = list(self.raw_folder.glob("*CS.pdf"))
        return matches[0] if matches else None

    def _render_pdf(self, pdf_path: Path) -> Path:
        doc = fitz.open(str(pdf_path))
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=config.PDF_RENDER_DPI)
        output = self.processed_folder / "rendered_cs_page.png"
        pix.save(str(output))
        doc.close()
        self.logger.info(f"Rendered PDF page to {output}")
        return output

    def _crop_table(self, image_path: Path) -> Path:
        img = cv2.imread(str(image_path))
        h, w, _ = img.shape
        table = img[int(h * 0.70):int(h * 1.00), int(w * 0.13):int(w * 0.96)]
        rotated = cv2.rotate(table, cv2.ROTATE_90_COUNTERCLOCKWISE)
        output = self.processed_folder / "rendered_cs_table.png"
        cv2.imwrite(str(output), rotated)
        self.logger.info(f"Cropped and rotated table to {output}")
        return output

    def _extract_with_ai(self, image_path: Path) -> list | None:
        if not config.GEMINI_API_KEY:
            self.logger.error("GEMINI_API_KEY not found in environment variables")
            return None

        genai.configure(api_key=config.GEMINI_API_KEY)
        model = genai.GenerativeModel(config.GEMINI_MODEL)
        pil_image = PIL.Image.open(str(image_path))
        response = model.generate_content([CS_PROMPT, pil_image])

        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            self.logger.error(f"JSON parsing failed: {e}")
            self.logger.debug(f"Raw response: {raw}")
            return None

    def _save_json(self, data, output_path: Path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)