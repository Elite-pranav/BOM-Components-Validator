"""
Cross-Section (CS) PDF extractor.

Reads engineering cross-section PDF drawings, renders them to images,
crops the BOM table region, and sends it to Google Gemini's vision API
for AI-powered text extraction. The extracted parts list is saved as
cs_bom.json in the processed folder.

Pipeline:
  1. Render PDF page to high-DPI PNG
  2. Crop and rotate the table region (top strip of the rendered portrait page)
  3. Send cropped image to Gemini with a structured prompt
  4. Parse the JSON response into a list of part dicts
  5. Clean whitespace from extracted strings
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
You are a precision table reader. Extract every row from this table image.

The table has these columns, left to right:

  REF  |  DESCRIPTION  |  QTY  |  MATERIAL/SPECIFICATION

RULES:

1. Extract EVERY row, top to bottom. Do not skip any.

2. Read ONLY what is printed in each cell.
   - If a cell is visually empty or blank, output null.
   - Do NOT fill blank cells with values from other rows.
   - Do NOT assume blank cells inherit from above or below.
   - Each row is independent.

3. Copy all text exactly as printed — preserve prefixes (ASTM, CI, IS, M.S.),
   suffixes (+ COATING, GR SS410), and punctuation.

4. QTY: output a number, or the string "AS REQD" if that is what is printed.

5. Skip the header row (the row containing REF. / DESCRIPTION / QTY. / MATERIAL.).

Return ONLY a raw JSON array. No markdown, no commentary.
Each element: {"ref": "...", "description": "...", "qty": ..., "material": ...}
"""


class CSExtractor(BaseExtractor):
    """Extracts BOM from Cross-Section (CS) PDF drawings."""

    # ── Table crop boundaries (as fractions of the rendered page) ──
    # The BOM table sits as a horizontal strip across the top of the
    # portrait-rendered page. These values were measured from a
    # 4132x5848 px render at config.PDF_RENDER_DPI.
    #
    #   y (height): 1.8 % -> 27.5 %   (covers all 4 column bands)
    #   x (width):  0.2 % -> 96.0 %   (covers every data row left to right)
    CROP_Y_START = 0.018
    CROP_Y_END = 0.275
    CROP_X_START = 0.002
    CROP_X_END = 0.960

    def extract(self) -> list:
        cs_pdf = self._find_cs_pdf()
        if not cs_pdf:
            self.logger.error(f"No CS PDF found in {self.raw_folder}")
            return []

        rendered = self._render_pdf(cs_pdf)
        cropped = self._crop_table(rendered)
        bom_data = self._extract_with_ai(cropped)

        if bom_data is not None:
            bom_data = self._clean_bom(bom_data)
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

        # Crop the BOM table strip from the top of the portrait-rendered page.
        # The table contains 4 horizontal bands (MATERIAL, QTY, DESCRIPTION, REF)
        # running across the full width of the drawing.
        y1 = int(h * self.CROP_Y_START)
        y2 = int(h * self.CROP_Y_END)
        x1 = int(w * self.CROP_X_START)
        x2 = int(w * self.CROP_X_END)

        table = img[y1:y2, x1:x2]
        rotated = cv2.rotate(table, cv2.ROTATE_90_COUNTERCLOCKWISE)

        output = self.processed_folder / "rendered_cs_table.png"
        cv2.imwrite(str(output), rotated)
        self.logger.info(
            f"Cropped table region y=[{y1}:{y2}], x=[{x1}:{x2}] "
            f"from {w}x{h} image and rotated to {output}"
        )
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

    def _clean_bom(self, bom_data: list) -> list:
        """Strip trailing whitespace and newlines from all string fields."""
        for row in bom_data:
            for key in ("ref", "description", "material"):
                if isinstance(row.get(key), str):
                    row[key] = row[key].strip()
        return bom_data

    def _save_json(self, data, output_path: Path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)