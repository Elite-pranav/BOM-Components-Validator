# BOM Components Validator

Extracts and compares Bill of Materials (BOM) data from three engineering document types — Cross-Section PDFs, Excel BOM spreadsheets, and SAP Data PDFs — using AI-powered vision extraction and structured parsing. Includes a React frontend for uploading, viewing, and downloading results.

## Prerequisites

- Python 3.11+
- Node.js 18+
- A Google Gemini API key (for CS drawing extraction)

## Setup

### 1. Clone and create virtual environment

```bash
git clone <repo-url>
cd BOM-Components-Validator
python -m venv venv
venv\Scripts\activate        # Windows PowerShell
# source venv/bin/activate   # macOS/Linux
```

### 2. Install backend dependencies

```bash
pip install -r backend/requirements.txt
```

### 3. Configure environment variables

Create `backend/.env` with your Gemini API key:

```
GEMINI_API_KEY=your_api_key_here
```

### 4. Install frontend dependencies

```bash
cd frontend
npm install
cd ..
```

## Running the Application

Open **two terminals** from the project root (`C:\BOM-Components-Validator`):

## Usage

### Web Interface

1. Open http://localhost:5173 in your browser
2. Upload three documents:
   - **Cross-Section PDF** (filename contains `CS`)
   - **BOM Excel** (`.XLSX` file, filename contains `BOM`)
   - **SAP Data PDF** (filename contains `SAP DATA`)
3. Click **Start Extraction** — all three extractors run concurrently
4. View results:
   - **Summary cards** showing item counts and key metadata
   - **Tabbed data tables** for CS BOM, Excel BOM, and SAP Data
   - **Download** extracted data as CSV or Excel
   - **Preview/download** the original uploaded documents
5. Click **New Extraction** to process a different set of documents

### CLI (Alternative)

Process all document folders in `backend/documents/raw/`:

```bash
cd backend
python main.py --cli
```

Process a specific folder:

```bash
cd backend
python main.py --cli 81351387
```

## Project Structure

```
BOM-Components-Validator/
├── backend/
│   ├── main.py                 — Unified entry point (web server + CLI)
│   ├── config.py               — Centralized configuration & paths
│   ├── requirements.txt        — Python dependencies
│   ├── .env                    — Environment variables (not committed)
│   ├── extractors/
│   │   ├── base.py             — Abstract base extractor class
│   │   ├── cs_extraction.py    — Cross-Section PDF → AI vision → BOM JSON
│   │   ├── bom_extraction.py   — Excel BOM spreadsheet → structured JSON
│   │   └── sap_extraction.py   — SAP Data PDF → key-value JSON
│   └── documents/
│       ├── raw/                — Sample input documents (CLI mode)
│       └── {identifier}/       — Per-upload folders (web mode)
│           ├── uploaded_documents/  — Original uploaded files
│           └── processed/           — Extraction outputs (JSON, PNG)
├── frontend/
│   ├── package.json
│   ├── vite.config.js          — Dev server config with API proxy
│   └── src/
│       ├── App.jsx             — Main app with upload → extract → results flow
│       ├── api/client.js       — Backend API fetch functions
│       ├── utils/              — CSV and Excel export utilities
│       └── components/         — React UI components
└── .gitignore
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Health check |
| `POST` | `/api/upload` | Upload 3 documents (multipart form) |
| `POST` | `/api/extract/{identifier}` | Run extraction pipeline |
| `GET` | `/api/results/{identifier}` | Fetch extracted data |
| `GET` | `/api/documents/{identifier}/{type}` | Preview/download uploaded file |

## Output Files

After extraction, the following files are saved in `backend/documents/{identifier}/processed/`:

| File | Description |
|------|-------------|
| `cs_bom.json` | Parts list extracted from CS drawing via AI |
| `bom.json` | Structured data parsed from Excel BOM |
| `sap_data.json` | Categorized SAP data (parts + metadata) |
| `sap_raw.json` | Raw key-value pairs from SAP PDF |
| `rendered_cs_page.png` | Full CS PDF page rendered at 500 DPI |
| `rendered_cs_table.png` | Cropped & rotated BOM table region |
| `{id}_extracted_cs.json` | Aliased copy of cs_bom.json |
| `{id}_extracted_bom.json` | Aliased copy of bom.json |
| `{id}_extracted_sap.json` | Aliased copy of sap_data.json |
