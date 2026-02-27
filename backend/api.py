from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.extractors.bom_extraction import BOMExtractor, PART_ABBREV
from backend.extractors.cs_extraction import CSExtractor
from backend.extractors.sap_extraction import PART_KEYS, SAPExtractor
from backend.main import process_folder

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"
FRONTEND_DIST_DIR = FRONTEND_DIR / "dist"

app = FastAPI(title="BOM Components Validator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _read_json_if_exists(path: Path):
    if not path.exists():
        return None
    with open(path, "r") as f:
        return json.load(f)


def _session_paths(session_id: str) -> tuple[Path, Path]:
    raw_folder = config.RAW_DIR / session_id
    processed_folder = config.PROCESSED_DIR / session_id
    raw_folder.mkdir(parents=True, exist_ok=True)
    processed_folder.mkdir(parents=True, exist_ok=True)
    return raw_folder, processed_folder


def _save_upload(upload_file: UploadFile, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        while chunk := upload_file.file.read(1024 * 1024):
            f.write(chunk)


def _normalize_text(value: str) -> str:
    return re.sub(r"[^A-Z0-9 ]+", " ", value.upper()).strip()


def _detect_canonical_from_text(value: str) -> tuple[str | None, str | None]:
    normalized = _normalize_text(value)

    for abbr in sorted(PART_ABBREV.keys(), key=len, reverse=True):
        abbr_normalized = _normalize_text(abbr)
        if normalized.startswith(abbr_normalized) or f" {abbr_normalized} " in f" {normalized} ":
            return PART_ABBREV[abbr], abbr

    for sap_key, canonical in PART_KEYS.items():
        sap_key_normalized = _normalize_text(sap_key)
        if normalized.startswith(sap_key_normalized) or f" {sap_key_normalized} " in f" {normalized} ":
            return canonical, sap_key

    return None, None


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/folders")
def list_folders():
    raw_folders = sorted(p.name for p in config.RAW_DIR.iterdir() if p.is_dir())
    items = []
    for folder_id in raw_folders:
        processed_dir = config.PROCESSED_DIR / folder_id
        items.append(
            {
                "folder_id": folder_id,
                "processed": processed_dir.exists(),
                "files": sorted(p.name for p in processed_dir.glob("*.json")) if processed_dir.exists() else [],
            }
        )
    return {"folders": items}


@app.post("/api/process/{folder_id}")
def process_by_folder(folder_id: str):
    raw_folder = config.RAW_DIR / folder_id
    if not raw_folder.exists() or not raw_folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {folder_id}")

    extraction_results = process_folder(raw_folder)
    return {
        "folder_id": folder_id,
        "result": {
            key: (len(value) if isinstance(value, list) else bool(value))
            for key, value in extraction_results.items()
        },
    }


@app.post("/api/extract/bom/{session_id}")
def extract_bom(session_id: str, file: UploadFile = File(...)):
    raw_folder, processed_folder = _session_paths(session_id)

    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        raise HTTPException(status_code=400, detail="BOM file must be an Excel file (.xlsx, .xlsm, .xltx, .xltm)")

    upload_path = raw_folder / f"{session_id}_BOM{ext}"
    _save_upload(file, upload_path)

    extracted = BOMExtractor(raw_folder=raw_folder, processed_folder=processed_folder).extract()
    return {
        "session_id": session_id,
        "file": upload_path.name,
        "rows_extracted": len(extracted),
    }


@app.post("/api/extract/sap/{session_id}")
def extract_sap(session_id: str, file: UploadFile = File(...)):
    raw_folder, processed_folder = _session_paths(session_id)

    ext = Path(file.filename or "").suffix.lower()
    if ext != ".pdf":
        raise HTTPException(status_code=400, detail="SAP file must be a PDF")

    upload_path = raw_folder / f"{session_id}_SAP DATA.pdf"
    _save_upload(file, upload_path)

    extracted = SAPExtractor(raw_folder=raw_folder, processed_folder=processed_folder).extract()
    return {
        "session_id": session_id,
        "file": upload_path.name,
        "parts_extracted": len((extracted or {}).get("parts", {})),
        "metadata_extracted": len((extracted or {}).get("metadata", {})),
    }


@app.post("/api/extract/cs/{session_id}")
def extract_cs(session_id: str, file: UploadFile = File(...)):
    raw_folder, processed_folder = _session_paths(session_id)

    ext = Path(file.filename or "").suffix.lower()
    if ext != ".pdf":
        raise HTTPException(status_code=400, detail="CS file must be a PDF")

    upload_path = raw_folder / f"{session_id}_CS.pdf"
    _save_upload(file, upload_path)

    extracted = CSExtractor(raw_folder=raw_folder, processed_folder=processed_folder).extract()
    return {
        "session_id": session_id,
        "file": upload_path.name,
        "rows_extracted": len(extracted),
    }


@app.post("/api/compare/{session_id}")
def compare_abbreviations(session_id: str):
    processed_folder = config.PROCESSED_DIR / session_id
    if not processed_folder.exists():
        raise HTTPException(status_code=404, detail=f"No processed data found for: {session_id}")

    bom_rows = _read_json_if_exists(processed_folder / "bom.json") or []
    cs_rows = _read_json_if_exists(processed_folder / "cs_bom.json") or []
    sap_data = _read_json_if_exists(processed_folder / "sap_data.json") or {}

    if not bom_rows and not cs_rows and not sap_data:
        raise HTTPException(status_code=400, detail="No extracted outputs found. Run extract APIs first.")

    comparison = defaultdict(lambda: {
        "in_bom": False,
        "in_sap": False,
        "in_cs": False,
        "bom_terms": set(),
        "sap_terms": set(),
        "cs_terms": set(),
    })

    for row in bom_rows:
        canonical = row.get("part_type")
        raw_description = row.get("description") or ""
        matched_abbrev = None
        if not canonical:
            canonical, matched_abbrev = _detect_canonical_from_text(raw_description)
        else:
            _, matched_abbrev = _detect_canonical_from_text(raw_description)

        if canonical:
            comparison[canonical]["in_bom"] = True
            if matched_abbrev:
                comparison[canonical]["bom_terms"].add(matched_abbrev)

    sap_parts = (sap_data or {}).get("parts", {})
    for part_name in sap_parts.keys():
        canonical = part_name
        _, matched_term = _detect_canonical_from_text(part_name)
        comparison[canonical]["in_sap"] = True
        comparison[canonical]["sap_terms"].add(matched_term or part_name)

    for row in cs_rows:
        desc = (row.get("description") or "").strip()
        canonical, matched_term = _detect_canonical_from_text(desc)
        if canonical:
            comparison[canonical]["in_cs"] = True
            comparison[canonical]["cs_terms"].add(matched_term or desc)

    comparison_rows = []
    for canonical in sorted(comparison.keys()):
        item = comparison[canonical]
        comparison_rows.append(
            {
                "component": canonical,
                "in_bom": item["in_bom"],
                "in_sap": item["in_sap"],
                "in_cs": item["in_cs"],
                "bom_terms": sorted(item["bom_terms"]),
                "sap_terms": sorted(item["sap_terms"]),
                "cs_terms": sorted(item["cs_terms"]),
            }
        )

    output_payload = {
        "session_id": session_id,
        "comparison": comparison_rows,
    }
    with open(processed_folder / "abbrev_comparison.json", "w") as f:
        json.dump(output_payload, f, indent=2)

    return output_payload


@app.get("/api/results/{folder_id}")
def get_results(folder_id: str):
    processed_dir = config.PROCESSED_DIR / folder_id
    if not processed_dir.exists() or not processed_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"No processed data found for: {folder_id}")

    return {
        "folder_id": folder_id,
        "bom": _read_json_if_exists(processed_dir / "bom.json"),
        "cs_bom": _read_json_if_exists(processed_dir / "cs_bom.json"),
        "sap_data": _read_json_if_exists(processed_dir / "sap_data.json"),
        "sap_raw": _read_json_if_exists(processed_dir / "sap_raw.json"),
        "comparison": _read_json_if_exists(processed_dir / "abbrev_comparison.json"),
    }


if FRONTEND_DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST_DIR / "assets")), name="assets")


@app.get("/{full_path:path}")
def serve_react_app(full_path: str):
    index_file = FRONTEND_DIST_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    raise HTTPException(
        status_code=404,
        detail="Frontend build not found. Run: cd frontend && npm install && npm run build",
    )
