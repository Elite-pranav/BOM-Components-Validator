"""
Centralized configuration for the BOM Components Validator.

Loads environment variables from backend/.env and exposes:
  - Directory paths (RAW_DIR, PROCESSED_DIR) for document I/O
  - Gemini API settings (key, model name)
  - PDF rendering parameters
"""

import os
from pathlib import Path
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
RAW_DIR = BACKEND_DIR / "documents" / "raw"
PROCESSED_DIR = BACKEND_DIR / "documents" / "processed"

load_dotenv(BACKEND_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash-lite"
PDF_RENDER_DPI = 500
