"""
Centralized configuration for the BOM Components Validator.

Loads environment variables from backend/.env and exposes:
  - Directory paths (DOCUMENTS_DIR, RAW_DIR, PROCESSED_DIR) for document I/O
  - Gemini API settings (key, model name)
  - PDF rendering parameters
"""

import os
from pathlib import Path
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
DOCUMENTS_DIR = BACKEND_DIR / "documents"
RAW_DIR = DOCUMENTS_DIR / "raw"
PROCESSED_DIR = DOCUMENTS_DIR / "processed"

load_dotenv(BACKEND_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash-lite"
PDF_RENDER_DPI = 500

CS_CROP_TOP    = 0.72
CS_CROP_BOTTOM = 0.990
CS_CROP_LEFT   = 0.13
CS_CROP_RIGHT  = 0.90
CS_N_STRIPS    = 3
CS_STRIP_OVERLAP = 0.08

CS_N_STRIPS      = 3      # Number of horizontal strips to split the table into
CS_STRIP_OVERLAP = 0.08   # Fractional overlap between adjacent strips (0–1)