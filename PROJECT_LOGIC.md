# BOM Components Validator - Complete Project Logic & Architecture

## Overview

A web application that validates pump engineering documents by extracting data from three document types (CS Drawing PDF, BOM Excel, SAP Data PDF), normalizing part names via a nomenclature alias system, and cross-referencing parts across all three sources to detect mismatches (MISSING parts and MATERIAL_MISMATCH).

---

## Architecture

```
Frontend (React 18 + Vite)  -->  Backend (FastAPI / Python)
     localhost:5173                    localhost:8000
```

### Backend Structure (`backend/`)

| File | Purpose |
|---|---|
| `config.py` | Centralized config: paths, Gemini API key/model, PDF DPI |
| `main.py` | FastAPI app, all API endpoints, CLI entry point |
| `extractors/base.py` | Abstract `BaseExtractor` class |
| `extractors/cs_extraction.py` | CS PDF extractor (uses **Google Gemini vision AI**) |
| `extractors/bom_extraction.py` | Excel BOM extractor (openpyxl + regex) |
| `extractors/sap_extraction.py` | SAP PDF extractor (pdfplumber + regex) |
| `comparator.py` | Part comparison engine + nomenclature alias system |
| `report.py` | PDF report generator (reportlab) |
| `nomenclature.json` | Global alias map: canonical name -> list of aliases |

### Frontend Structure (`frontend/src/`)

| Component | Purpose |
|---|---|
| `App.jsx` | Main state machine: upload -> extracting -> results -> comparing -> validation |
| `UploadSection` | Three FileDropZone components for CS/BOM/SAP |
| `FileDropZone` | Drag-and-drop file upload widget |
| `ProgressIndicator` | Loading spinner during extraction/comparison |
| `ResultsSection` | Shows extraction results with SummaryCards, DataTabs, DocumentPreview |
| `SummaryCards` | Cards showing count of parts extracted from each source |
| `DataTabs` | Tabbed view of CS/BOM/SAP extracted data in tables |
| `DataTable` | Generic table renderer |
| `DocumentPreview` | Links to view/download uploaded documents |
| `ActionBar` | Export CSV/Excel buttons |
| `ValidationSection` | Shows discrepancies + unresolved parts for user review |
| `DiscrepancyCard` | Card per flagged part: agree (confirm error) or disagree (map to canonical) |
| `UnresolvedCard` | Card per unresolved part: select correct canonical mapping |
| `api/client.js` | API client functions (upload, extract, compare, validate, etc.) |

---

## Data Flow

### Step 1: Upload
- User uploads 3 files via frontend
- Backend saves to `backend/documents/{identifier}/uploaded_documents/`
- Identifier = 8-digit number extracted from filenames via regex `(\d{8})`

### Step 2: Extraction (3 extractors run concurrently via ThreadPoolExecutor)

#### CS Extraction (`cs_extraction.py`) — **USES LLM (Google Gemini)**
1. Find PDF matching `*CS.pdf` in raw folder
2. Render PDF page 0 to PNG at 500 DPI using PyMuPDF (fitz)
3. Crop table region: `y: 70%-100%, x: 13%-96%` — **HARDCODED crop coordinates**
4. Rotate cropped image 90 degrees counterclockwise
5. Send to **Gemini 2.5 Flash Lite** vision API with structured prompt
6. Parse JSON response -> list of `{ref, description, qty, material}`
7. Save as `cs_bom.json`

#### BOM Extraction (`bom_extraction.py`) — **Regex + Static Mapping**
1. Find Excel matching `*BOM.XLSX`
2. Read rows via openpyxl (skip header)
3. **Hardcoded column indices** (0=Item#, 1=Component#, 2=Description, 3=Qty, 4=Unit, 5=Text1, 6=Text2, 7=SortString)
4. Match description to part type using `PART_ABBREV` dict (longest-match-first)
5. Extract material code via regex patterns (`SS\d{3}`, `CF\d+M?`, `GGG\d+`, etc.)
6. Detect coating from `+COAT` in description
7. Categorize via `SORT_CATEGORIES` from Sort String column
8. Save as `bom.json`

#### SAP Extraction (`sap_extraction.py`) — **Text Parsing + Static Mapping**
1. Find PDF matching `*SAP DATA.pdf`
2. Extract text via pdfplumber
3. Parse key-value pairs using two patterns:
   - `Key * Value` (asterisk separator)
   - `Key    Value` (2+ spaces separator)
4. Categorize into `parts` vs `metadata` using `PART_KEYS` dict
5. Extract material codes via regex
6. Save as `sap_data.json` and `sap_raw.json`

### Step 3: Comparison (`comparator.py`) — Two-Pass Architecture

1. Load extracted JSON files (cs_bom.json, bom.json, sap_data.json)
2. Load `nomenclature.json` — the global alias map
3. **Normalize** each source's parts:
   - For each part description, try to resolve to a canonical name via nomenclature
   - Fallback: `_try_partial_resolve()` — try first 4, 3, 2 words as prefix
   - CS: Skip fasteners/generics (gaskets, washers, studs, etc.)
   - BOM: Try resolving `part_type` first, then `description`
   - SAP: Direct key lookup from `PART_KEYS`
4. **Cross-reference** all canonical names found across all 3 sources
5. **Pass 1 — Rigid String Comparison** (via `materials.py`):
   - Strip spec prefixes (ASTM A276 GR, IS:2062 GR-B, etc.)
   - Normalize dots (M.S. -> MS)
   - Separate coating from material string (compare independently)
   - Handle `/` separators (CF8M/SS410H -> set intersection matching)
   - Only clears obvious matches — conservative, zero false-positive risk
6. **Pass 2 — Gemini LLM Comparison** (batched single API call):
   - Remaining unresolved material pairs sent to Gemini
   - LLM evaluates both part name match and material match
   - Returns match/mismatch + 1-line explanation per part
   - Handles domain knowledge: casting/wrought equivalents, heat treatment, material families
7. Detect discrepancy types:
   - **MISSING**: Part present in some sources but not all
   - **MATERIAL_MISMATCH**: Material codes genuinely differ (confirmed by rigid or LLM)
   - **COATING_MISMATCH**: Base material matches but coating specification differs
   - **QUANTITY_MISMATCH**: Quantities differ across sources
   - **NAME_MISMATCH**: LLM determines part names don't refer to same component
8. Each discrepancy includes a `reason` field with 1-line explanation
9. Save as `comparison_results.json`

### Step 4: Validation (User Review)
- User reviews each discrepancy:
  - **Agree**: Confirms it's a real error
  - **Disagree**: Maps the part to a different canonical name (adds alias to nomenclature.json)
- Unresolved parts: User selects correct canonical mapping
- On submit: `apply_validation()` persists decisions + updates nomenclature
- Report generation via reportlab (PDF with metadata, comparison table, confirmed errors)

---

## Key Static/Hardcoded Elements

| Element | Location | What's hardcoded |
|---|---|---|
| CS PDF crop region | `cs_extraction.py:83` | `h*0.70:h*1.00, w*0.13:w*0.96` |
| Excel column indices | `bom_extraction.py:23-30` | Columns 0-7 |
| Part abbreviation map | `bom_extraction.py:32-77` | 40+ abbreviations |
| SAP part keys | `sap_extraction.py:28-48` | 18 part keys |
| Material regex patterns | `materials.py` | Single source of truth (shared by all extractors and comparator) |
| Fastener keywords | `comparator.py:349-354` | 14 keywords |
| Sort categories | `bom_extraction.py:79-85` | 5 categories |
| Gemini model | `config.py:22` | `gemini-2.5-flash-lite` |
| PDF render DPI | `config.py:23` | 500 |
| CORS origin | `main.py:98` | `http://localhost:5173` |
| Identifier pattern | `main.py:103` | `\d{8}` |
| File detection | `main.py:117-124` | Filename pattern matching for doc types |

---

## Nomenclature System

The `nomenclature.json` file is the **central alias dictionary**. Structure:
```json
{
  "Canonical Name": {
    "aliases": ["ALIAS1", "Alias2", "ABBREV", ...]
  }
}
```

- Currently has ~50 canonical parts with 200+ aliases
- Reverse map built at init: `{ALIAS_UPPER: canonical_name}`
- Self-learning: when users disagree with a mismatch, the new alias is added
- Case-insensitive resolution

---

## Technologies Used

- **Backend**: Python, FastAPI, Google Gemini AI (vision), PyMuPDF, OpenCV, pdfplumber, openpyxl, reportlab
- **Frontend**: React 18, Vite, CSS Modules, react-icons, xlsx (for export)
- **AI Model**: Gemini 2.5 Flash Lite (for CS PDF table extraction only)
