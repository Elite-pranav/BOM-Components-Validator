"""
Centralized configuration for the BOM Components Validator.

Loads environment variables from backend/.env and exposes:
  - Directory paths (DOCUMENTS_DIR, RAW_DIR, PROCESSED_DIR) for document I/O
  - OpenAI API settings (key, model name)
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

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
