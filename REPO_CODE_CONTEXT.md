# BOM Components Validator — Repository Code Context

---

## PART 1: Codebase Overview & Architecture

### What This Project Does

This is a web application that validates pump engineering documents by:
1. Accepting three document types for a given pump order (CS Drawing PDF, BOM Excel spreadsheet, SAP Data PDF)
2. Extracting structured part/material data from each document
3. Normalizing part names via a nomenclature alias system (`nomenclature.json`)
4. Cross-referencing material data across all three sources to detect mismatches
5. Generating a PDF validation report with authority-guided discrepancy resolution

The system is purpose-built for vertical turbine pump (VT pump) manufacturing QA, where three different documents must agree on which material each part is made from before a pump ships.

---

### High-Level Architecture

```
Frontend (React 18 + Vite)      Backend (FastAPI + Python)
     localhost:5173        ──►       localhost:8000
```

**Document flow per pump order:**
```
Raw Documents (PDF/XLSX)
        │
        ├─► CSExtractor    → cs_bom.json       (AI vision via Gemini)
        ├─► BOMExtractor   → bom_data.json     (openpyxl Excel parsing)
        └─► SAPExtractor   → sap_data.json     (pdfplumber PDF parsing)
                │
                ▼
          comparator.py
         ┌──────────────────────────────────────────┐
         │  Pass 1 (Rigid): Rule-based, deterministic│
         │  Pass 2 (LLM):   Gemini for family conflicts│
         └──────────────────────────────────────────┘
                │
                ▼
        comparison_results.json
                │
                ▼
          report.py → {identifier}_validation_report.pdf
```

---

### Directory Structure

```
BOM-Components-Validator/
├── backend/
│   ├── config.py                   # Centralized config: paths, Gemini API key/model, DPI
│   ├── main.py                     # FastAPI app + CLI entry point
│   ├── comparator.py               # Core comparison engine (two-pass: rigid + LLM)
│   ├── materials.py                # Material normalization, family equivalence, matching
│   ├── report.py                   # PDF report generator (reportlab)
│   ├── nomenclature.json           # Alias map: canonical part name → list of aliases
│   ├── requirements.txt
│   ├── extractors/
│   │   ├── base.py                 # Abstract BaseExtractor class
│   │   ├── cs_extraction.py        # CS PDF → Gemini vision AI → cs_bom.json
│   │   ├── bom_extraction.py       # BOM XLSX → openpyxl → bom_data.json
│   │   └── sap_extraction.py       # SAP PDF → pdfplumber → sap_data.json
│   └── documents/
│       ├── raw/{identifier}/       # Uploaded source documents
│       └── processed/{identifier}/ # Extracted JSON + rendered images + report
└── frontend/
    ├── src/
    │   ├── App.jsx                 # Root state machine (5 steps)
    │   ├── api/client.js           # All fetch() wrappers for backend API
    │   └── components/
    │       ├── UploadSection/      # Three file drop zones (CS, BOM, SAP)
    │       ├── FileDropZone/       # Drag-and-drop widget
    │       ├── ProgressIndicator/  # Spinner shown during extraction/comparison
    │       ├── ResultsSection/     # Shows extraction output (tabs per source)
    │       ├── SummaryCards/       # Part counts per source
    │       ├── DataTabs/           # Tabbed table view: CS / BOM / SAP data
    │       ├── DataTable/          # Generic sortable table component
    │       ├── ActionBar/          # Export CSV/Excel buttons
    │       ├── DocumentPreview/    # In-browser preview of uploaded files
    │       ├── ValidationSection/  # Discrepancy review + decision submission
    │       ├── DiscrepancyCard/    # Per-part agree/disagree decision UI
    │       ├── UnresolvedCard/     # Map unresolved parts to canonical names
    │       ├── Header/Footer/      # App shell
    │       └── utils/              # exportCsv.js, exportExcel.js
    └── package.json                # React 18, Vite, react-icons, xlsx
```

---

### Backend: File-by-File Breakdown

#### `backend/config.py`
Centralizes all configuration:
- **Directory paths**: `DOCUMENTS_DIR`, `RAW_DIR`, `PROCESSED_DIR` — all relative to `backend/`
- **Gemini API**: `GEMINI_API_KEY` (from `.env`), `GEMINI_MODEL = "gemini-2.5-flash-lite"`
- **PDF rendering**: `PDF_RENDER_DPI = 500`
- **CS crop parameters**: fractional coordinates (`CS_CROP_TOP/BOTTOM/LEFT/RIGHT`) for cropping the BOM table out of the CS drawing page

#### `backend/main.py`
Dual-mode entry point:
- **Web server mode** (default): Starts FastAPI on port 8000. All business logic lives in the API endpoints.
- **CLI mode** (`--cli` flag): Batch-processes folders from `documents/raw/`

**Key API endpoints:**
| Method | Path | What it does |
|--------|------|-------------|
| POST | `/api/upload` | Saves CS PDF, BOM XLSX, SAP PDF; parses 8-digit identifier from filename |
| POST | `/api/extract/{id}` | Runs CSExtractor (sequential), then BOMExtractor + SAPExtractor (parallel via ThreadPoolExecutor) |
| GET | `/api/results/{id}` | Returns `cs_bom.json`, `bom_data.json`, `sap_data.json` |
| GET | `/api/documents/{id}/{type}` | Serves the original uploaded file (cs/bom/sap) |
| POST | `/api/compare/{id}` | Runs the two-pass comparison engine, saves `comparison_results.json` |
| POST | `/api/validate/{id}` | Applies user agree/disagree decisions, saves `validation_status.json` |
| GET | `/api/report/{id}` | Generates PDF report on-demand if not already present |
| GET | `/api/nomenclature` | Returns all canonical part names from nomenclature.json |

**Extraction order logic**: CSExtractor runs first (sequential) because it has an async render+AI pipeline. BOMExtractor and SAPExtractor run in parallel since they are independent of CS.

**Identifier parsing**: Extracts the first 8-digit number from any of the three uploaded filenames. This becomes the folder name and key for all subsequent operations.

#### `backend/extractors/base.py`
Abstract `BaseExtractor(ABC)` with:
- Constructor sets `raw_folder`, `processed_folder`, creates the processed dir, sets up per-class logger
- Abstract `extract()` returning `dict | list`
- `_save_json(data, path)` helper

#### `backend/extractors/cs_extraction.py` — `CSExtractor`
Extracts the BOM table from a Cross-Section engineering drawing PDF using AI vision.

**Pipeline:**
1. `_find_cs_pdf()` — globs for `*CS.pdf` in raw folder
2. `_render_pdf()` — renders page 0 to PNG at 500 DPI using PyMuPDF (fitz)
3. `_crop_table()` — crops the BOM strip from the top of the portrait-rendered page using fractional coordinates, then rotates 90° CCW so columns become rows
4. `_extract_with_ai()` — sends cropped image to Gemini with a strict table-reading prompt; expects raw JSON array `[{ref, description, qty, material}]`
5. `_clean_bom()` — strips whitespace from all string fields

Output saved as `cs_bom.json`. The CS drawing BOM strip spans columns: REF | DESCRIPTION | QTY | MATERIAL/SPECIFICATION.

**Key constants:** `CROP_Y_START=0.018`, `CROP_Y_END=0.275`, `CROP_X_START=0.002`, `CROP_X_END=0.960` — these are fractions of the 500 DPI rendered page size.

#### `backend/extractors/bom_extraction.py` — `BOMExtractor`
Reads the SAP-exported PUMP BOM Excel file (`.XLSX`).

**Column mapping (0-indexed):**
- 0: item_number, 1: component_number, 2: description, 3: quantity, 4: unit, 5: text1, 6: text2, 7: sort_string

Skips header row (row 0) and fully empty rows. Quantities are kept as int/float as stored by Excel. Empty strings are normalized to `None`. Output saved as `bom_data.json`.

#### `backend/extractors/sap_extraction.py` — `SAPExtractor`
Extracts key-value pairs from an SAP DATA PDF using `pdfplumber` table detection.

- Detects multi-line keys (structural blocks) and skips them, except extracting the "Design Text" block from them
- Strips trailing SAP asterisks from keys
- Skips `Characteristics` / `Value` header rows
- Normalizes empty strings to `None`
- Preserves duplicate keys as separate list entries

Output schema: `{"entries": [{"key": str, "value": str}], "design_text": str | null}` saved as `sap_data.json`.

#### `backend/comparator.py` — Two-Pass Comparison Engine

This is the core intelligence of the system.

**Two-pass architecture:**

**Pass 1 — Rigid (deterministic, ~90% of parts):**
- Normalizes all raw material strings → codes → semantic families using `materials.py`
- Applies authority model: SAP > CS > BOM for material truth
- Applies absence rules: SAP absence = always OK; BOM absence = only flag for `MAJOR_WETTED_PARTS`; CS absence = flag for major structural parts
- CS sanity check: if a consumable material (rubber, cotton) appears on a structural part in CS, it's flagged as an extraction error and excluded from comparison
- Cross-source confidence check: if BOM + SAP agree but CS disagrees → CS flagged as likely row-span extraction error, cleared automatically
- Coating resolution: if SAP metadata says `Coating Reqd By Customer: YES`, BOM coating flag never causes a mismatch

**Pass 2 — LLM (only genuine family conflicts):**
- Only parts where ≥2 sources have material data AND families genuinely differ reach this pass
- Sends a batch prompt to Gemini with context about pump model, stages, and each part's materials
- LLM returns CLEAR or FLAGGED per part, with authority, correct_material, and discrepancy details
- Falls back to conservative MANUAL_REVIEW if API is unavailable

**`Nomenclature` class:**
- Loads `nomenclature.json` on init, builds a reverse alias map (`ALIAS.UPPER() → canonical`)
- `resolve(name)` — looks up any alias or canonical name (case-insensitive)
- `add_alias(canonical, alias)` — adds new alias and saves atomically via tmp file swap
- `get_all_canonical()` — used by the `/api/nomenclature` endpoint

**Normalization functions:**
- `_normalize_cs()` — for each CS entry, tries multiple cleaned candidates (`_clean_cs_description()`) before marking unresolved
- `_normalize_bom()` — extracts part prefix (e.g. "IMP", "STRAINER") then resolves; also extracts material from BOM description string
- `_normalize_sap()` — resolves each SAP key; non-part keys become metadata (stages, pump name, coating required, etc.)

**Output (`comparison_results.json`):**
```json
{
  "identifier": "81355130",
  "timestamp": "...",
  "summary": {"total_canonical_parts": N, "discrepancies_found": M, "unresolved_parts": K},
  "parts": [{"canonical_name", "cs", "bom", "sap", "material_comparison", "discrepancies"}],
  "unresolved": [{"source", "original_name", "ref"}],
  "sap_metadata": {"No of Stages": "10", "VT pump Common Name": "...", ...}
}
```

**`apply_validation()`:** Processes user agree/disagree decisions:
- `agree` → records confirmed discrepancy
- `disagree` → calls `nomenclature.add_alias()` to map the unresolved name to a canonical

#### `backend/materials.py` — Material Intelligence

Single source of truth for all material handling across the system:

**Key data structures:**
- `MATERIAL_PATTERNS` — ordered regex list for extracting material codes from text (CA*, CF*, SS*, GGG*, FG*, EN*, WCB, HTS, CI, MS, etc.)
- `SPEC_PREFIXES` — regex list for stripping standards prefixes (ASTM A276 GR, CI IS 210 GR, IS:2062 GR-B, etc.)
- `MATERIAL_FAMILY_MAP` — maps normalized code → canonical family: SS410T/SS410H/CA15 → "SS410"; CF8M/CF3M → "SS316"; WCB → "MS"; CI → "FG260"; etc.
- `CONSUMABLE_KEYWORDS` — cotton, rubber, nitrile, PTFE, etc. — used to detect CS extraction errors
- `COATING_BRAND_KEYWORDS` — Champion AF, Asian Glass, Wilo Green, Epoxy — another class of CS extraction error
- `MAJOR_WETTED_PARTS` / `STRUCTURAL_PART_NAMES` — populated at runtime from `nomenclature.json` `"type"` fields

**Key functions:**
- `normalize_for_rigid_comparison(raw)` → `(set[codes], has_coating)` — strips specs, handles composites, splits "/" variants
- `get_material_family(code)` → canonical family string or None
- `rigid_materials_match(materials, coatings, coating_required)` → `{result, normalized, families, coating_match, explanation}`
- `is_consumable_material(material)` → True if consumable AND no structural alloy present (composite-safe)
- `is_coating_brand(material)` → True if text is a coating product name
- `_resolve_composite(upper)` → if material is "CUTLESS RUBBER + SS410 SHELL", returns "SS410"

**`nomenclature.json` part type system:**
- `"wetted_structural"` — major wetted parts; BOM absence is flagged; consumable CS materials = extraction error
- `"structural"` — structural parts; consumable CS materials = extraction error; BOM absence OK
- `"consumable"` — packing, seals; not compared for material
- `"accessory"` — bought-out items; not compared

#### `backend/report.py` — PDF Report Generator

Generates a structured A4 PDF using `reportlab` with these sections:

1. **Title** — Identifier + generation timestamp
2. **Pump Metadata** — Dynamic table from SAP metadata. Priority keys shown first (pump name, stages, region, flow, etc.). Skips internal admin/dimensional keys. Capped at 16 rows.
3. **Discrepancy Alert** — Headline section for all FLAGged parts. Shows Part Name | Conflict (what each source says) | Assessment (authority guidance: who's wrong and what's correct) | Confirmed status
4. **Extraction Warnings** — Separate amber-colored section for CS_EXTRACTION_WARNING parts (not material errors — just extractor issues to fix)
5. **Summary** — Counts of total parts, flags, warnings, unresolved, user confirmations/dismissals
6. **Full Part Comparison Table** — All parts with CS/BOM/SAP material values and OK/FLAG/WARN/ERROR status
7. **Appendix A** — Confirmed discrepancies with reason
8. **Appendix B** — Dismissed false positives with canonical remapping

**Color palette:** Dark slate headers, red for flags/alerts, amber for warnings, off-white alternating rows.

---

### Frontend: Component Breakdown

**`App.jsx` — State Machine**
Manages 5 steps via `useState("upload")`:
1. `upload` → shows UploadSection (3 file inputs)
2. `extracting` → shows ProgressIndicator (spinner)
3. `results` → shows ResultsSection (extraction output)
4. `comparing` → shows ProgressIndicator
5. `validation` → shows ValidationSection (discrepancy review)

State held: `files` (CS/BOM/SAP File objects), `identifier` (8-digit string), `results` (extracted JSON), `comparison` (comparison JSON), `error`.

**`api/client.js` — API Wrappers**
All fetch() calls to `BASE = "/api"` (proxied by Vite to localhost:8000). Functions: `uploadDocuments`, `triggerExtraction`, `getResults`, `getDocumentUrl`, `runComparison`, `submitValidation`, `getNomenclature`, `getReportUrl`.

**`UploadSection`** — Three `FileDropZone` components for CS PDF, BOM XLSX, SAP PDF. "Start Extraction" button enabled only when all three files are selected.

**`ResultsSection`** — Displays extraction output with tabs per source (CS / BOM / SAP), summary cards (part counts), document preview panel, and export buttons. "Compare Parts" button triggers Pass 1+2 comparison.

**`ValidationSection`** — Reviews discrepancies one by one using `DiscrepancyCard` (agree/disagree per part) and `UnresolvedCard` (map unresolved parts to canonical names). Submits all decisions to `/api/validate/{id}`. After submission, shows "Download Report" link.

**`DataTabs` + `DataTable`** — Tabbed display of raw extracted data from each document source. DataTable is a generic sortable table.

**`DiscrepancyCard`** — Shows each flagged part with CS/BOM/SAP values, the discrepancy reason, and agree/disagree toggle. "Disagree" allows mapping to a canonical name from the nomenclature.

**`UnresolvedCard`** — For parts not matched to any canonical name. Shows source and original name, provides a searchable dropdown of all canonical names.

**`ActionBar`** — Export buttons: download the active tab's data as CSV or Excel.

**`DocumentPreview`** — In-browser preview of the original uploaded files via the `/api/documents/{id}/{type}` endpoint.

---

### Data Flow: End-to-End

```
User uploads CS.pdf + BOM.xlsx + SAP.pdf
    │
    ▼
POST /api/upload → saved to documents/{id}/uploaded_documents/
    │
    ▼
POST /api/extract/{id}
    ├── CSExtractor: render → crop → Gemini AI → cs_bom.json
    ├── BOMExtractor: openpyxl parse → bom_data.json
    └── SAPExtractor: pdfplumber parse → sap_data.json
    │
    ▼
GET /api/results/{id} → returns all three JSON files to frontend
    │
    ▼
POST /api/compare/{id}
    ├── Load part type sets from nomenclature.json
    ├── Normalize CS/BOM/SAP parts (alias resolution, material extraction)
    ├── Pass 1: Rigid rule-based evaluation (absence checks, family matching)
    ├── Pass 2: LLM evaluation for genuine family conflicts (Gemini)
    └── Save comparison_results.json
    │
    ▼
POST /api/validate/{id}
    ├── Apply user agree/disagree decisions
    ├── Add new aliases to nomenclature.json (for disagree + mapping)
    └── Save validation_status.json
    │
    ▼
GET /api/report/{id} → generates PDF report on-demand
```

---

### Key Design Decisions

1. **Authority model**: SAP → CS → BOM priority for material truth. SAP absence never flagged. BOM absence only flagged for major wetted parts.

2. **Nomenclature alias system**: Every part name variant across all three document types maps to a single canonical name. This is how "DIFF", "DIFFUSER (DELY)", "BOWL", "CASING" all resolve to "Diffuser". The system learns new aliases when users "disagree" with an unresolved mapping.

3. **CS extraction warnings**: CS drawings often have PDF row-spanning issues where a material from one row leaks into an adjacent row. The system detects this (consumable on structural, or BOM+SAP both disagree with CS) and auto-clears rather than false-flagging.

4. **Two-pass comparison**: ~90% of parts cleared deterministically via rigid rules. Only genuine alloy family conflicts (typically 2-5 parts per pump) reach the Gemini LLM, keeping API costs low.

5. **Gemini for CS extraction**: The CS drawing BOM is embedded as a rotated table strip inside a large engineering PDF. Rather than complex table-detection logic, the system renders the page at 500 DPI, crops the strip, and sends it to Gemini's vision API for structured extraction.

6. **File organization by identifier**: All documents and results are stored under `backend/documents/{8-digit-id}/`. This enables multi-pump processing and easy result retrieval.

---

## PART 2: Concurrent Changes Log

> This section documents every functional change made to the codebase after the initial context snapshot (2026-06-27). Each entry records what changed, why, and which files were modified.

---

<!-- New change entries go below this line, in reverse-chronological order (newest first) -->

