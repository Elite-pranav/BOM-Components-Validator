"""
Cross-Section (CS) PDF extractor.

Reads engineering cross-section PDF drawings, renders them to images,
crops the BOM table region, and sends it to Google Gemini's vision API
for AI-powered text extraction. The extracted parts list is saved as
bom.json in the processed folder.

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
This image contains a parts list / Bill of Materials (BOM) table from an engineering drawing.

Extract ALL rows from the table and return the data as a JSON array.
Each row should be an object with these keys:
- "ref": the part reference number
- "description": part description
- "qty": quantity (as a number, or "AS REQD" if applicable)
- "material": material specification

Return ONLY the raw JSON array with no explanation, no markdown, no code blocks.
Example format:
[
  {"ref": "1030", "description": "DIFFUSER (STAGE)", "qty": 1, "material": "GGG50 + COATING"},
  ...
]
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
            self._save_json(bom_data, self.processed_folder / "bom.json")
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
        output = self.processed_folder / "rendered_page.png"
        pix.save(str(output))
        doc.close()
        self.logger.info(f"Rendered PDF page to {output}")
        return output

    def _crop_table(self, image_path: Path) -> Path:
        img = cv2.imread(str(image_path))
        h, w, _ = img.shape
        table = img[int(h * 0.70):int(h * 1.00), int(w * 0.13):int(w * 0.96)]
        rotated = cv2.rotate(table, cv2.ROTATE_90_COUNTERCLOCKWISE)
        output = self.processed_folder / "cs_table.png"
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
