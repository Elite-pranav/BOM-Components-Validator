# BOM Components Validator

Extracts Bill of Materials (BOM) data from engineering PDF drawings using AI-powered vision extraction.

## Setup

```bash
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r backend/requirements.txt
```

Create `backend/.env` with your API key:

```
GEMINI_API_KEY = "AIzaSyDUvGX1_U2GmEK6QcNQEyqslCcCND5rzGc"
```

## Usage

Process all document folders:

```bash
python -m backend.main
```

Process a specific folder:

```bash
python -m backend.main 81351387
```

## Project Structure

- `backend/main.py` — Entry point, orchestrates all extractors concurrently
- `backend/config.py` — Centralized configuration and path management
- `backend/extractors/` — Document extraction modules
  - `cs_extraction.py` — Cross-section PDF BOM extraction
  - `bom_extraction.py` — Excel BOM extraction (planned)
  - `sap_extraction.py` — SAP DATA PDF extraction (planned)
- `backend/documents/raw/` — Input documents
- `backend/documents/processed/` — Extraction outputs
