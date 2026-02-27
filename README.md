# BOM Components Validator

Extracts Bill of Materials (BOM) data from engineering PDF drawings using AI-powered vision extraction.

## Setup (Single Python Environment)

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r backend/requirements.txt
```

Install frontend dependencies:

```bash
cd frontend
npm install
cd ..
```

Use this single Python environment (`.venv`) for all backend/API work.
Frontend uses Node.js (`npm`), which is separate from Python virtual environments.

Create `backend/.env` with your API key:

```
GEMINI_API_KEY = "your_api_key_here"
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

Run the web app (API + frontend):

### Development mode (React + API)

Terminal 1 (backend API):

```bash
uvicorn backend.api:app --reload
```

Terminal 2 (React app):

```bash
cd frontend
npm run dev
```

Open `http://127.0.0.1:5173` in your browser.

### Production-style mode (FastAPI serves React build)

```bash
cd frontend
npm run build
cd ..
uvicorn backend.api:app --reload
```

Open `http://127.0.0.1:8000` in your browser.

Frontend capabilities:
- Upload BOM/SAP/CS files for a session
- Trigger extraction via dedicated APIs (BOM, SAP, CS)
- Run abbreviation comparison across extracted outputs
- Load/view processed outputs (`bom.json`, `cs_bom.json`, `sap_data.json`, `sap_raw.json`)
- Display summary counts and tabular views for BOM/CS/SAP parts

## API Endpoints

- `POST /api/extract/bom/{session_id}` — upload BOM Excel and run BOM extraction
- `POST /api/extract/sap/{session_id}` — upload SAP PDF and run SAP extraction
- `POST /api/extract/cs/{session_id}` — upload CS PDF and run CS extraction
- `POST /api/compare/{session_id}` — compare abbreviations/components across BOM/SAP/CS extracted data
- `GET /api/results/{session_id}` — fetch extracted output + comparison JSON

## Project Structure

- `backend/main.py` — Entry point, orchestrates all extractors concurrently
- `backend/api.py` — FastAPI service for processing and serving frontend
- `backend/config.py` — Centralized configuration and path management
- `backend/extractors/` — Document extraction modules
  - `cs_extraction.py` — Cross-section PDF BOM extraction
  - `bom_extraction.py` — Excel BOM extraction (planned)
  - `sap_extraction.py` — SAP DATA PDF extraction (planned)
- `backend/documents/raw/` — Input documents
- `backend/documents/processed/` — Extraction outputs
- `frontend/` — React (Vite) frontend
