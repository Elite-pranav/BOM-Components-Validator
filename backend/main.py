"""
BOM Components Validator — unified entry point.

Starts the FastAPI web server by default. Use --cli flag for batch
processing without the web interface.

Usage:
    python main.py                      # start web server on port 8000
    python main.py --cli                # process all folders in documents/raw/
    python main.py --cli 81351387       # process one folder
"""

import json
import logging
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Ensure the project root is on sys.path so `backend.*` imports work
# regardless of which directory this script is invoked from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend import config
from backend.extractors import CSExtractor, BOMExtractor, SAPExtractor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

EXTRACTORS = [CSExtractor, BOMExtractor, SAPExtractor]


# ── Core extraction logic ────────────────────────────────────────────────────

def process_folder(folder: Path, processed: Path | None = None):
    """Run all extractors on a single document folder."""
    folder_id = folder.name
    if processed is None:
        processed = config.PROCESSED_DIR / folder_id
    results = {}

    with ThreadPoolExecutor(max_workers=len(EXTRACTORS)) as pool:
        futures = {}
        for ExtractorClass in EXTRACTORS:
            ext = ExtractorClass(raw_folder=folder, processed_folder=processed)
            future = pool.submit(ext.extract)
            futures[future] = ExtractorClass.__name__

        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
                logger.info(f"{name} completed for {folder_id}")
            except Exception as e:
                logger.error(f"{name} failed for {folder_id}: {e}")
                results[name] = None

    return results


# ── CLI mode ─────────────────────────────────────────────────────────────────

def run_cli(args: list[str]):
    """Batch-process document folders from the command line."""
    if args:
        folders = [config.RAW_DIR / arg for arg in args]
    else:
        folders = sorted(p for p in config.RAW_DIR.iterdir() if p.is_dir())

    logger.info(f"Processing {len(folders)} document folder(s)")

    for folder in folders:
        if not folder.exists():
            logger.error(f"Folder not found: {folder}")
            continue
        logger.info(f"--- Processing {folder.name} ---")
        process_folder(folder)


# ── Web API mode ─────────────────────────────────────────────────────────────

def create_app():
    """Build and return the FastAPI application."""
    from fastapi import FastAPI, File, HTTPException, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse

    app = FastAPI(title="BOM Components Validator API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    IDENTIFIER_RE = re.compile(r"(\d{8})")

    def _get_dirs(identifier: str) -> tuple[Path, Path]:
        base = config.DOCUMENTS_DIR / identifier
        return base / "uploaded_documents", base / "processed"

    def _parse_identifier(*filenames: str) -> str | None:
        for name in filenames:
            m = IDENTIFIER_RE.search(name)
            if m:
                return m.group(1)
        return None

    def _detect_doc_type(filename: str) -> str | None:
        upper = filename.upper()
        if upper.endswith(".XLSX") or "BOM" in upper:
            return "bom"
        if "SAP" in upper:
            return "sap"
        if "CS" in upper and upper.endswith(".PDF"):
            return "cs"
        return None

    def _read_results(processed_dir: Path) -> dict:
        response_data = {}
        for key, filename in [
            ("cs_bom", "cs_bom.json"),
            ("bom_excel", "bom.json"),
            ("sap_data", "sap_data.json"),
        ]:
            json_path = processed_dir / filename
            if json_path.exists():
                with open(json_path) as f:
                    response_data[key] = json.load(f)
            else:
                response_data[key] = None
        return response_data

    # ── Endpoints ────────────────────────────────────────────────────────

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.post("/api/upload")
    async def upload_documents(
        cs_pdf: UploadFile = File(...),
        bom_xlsx: UploadFile = File(...),
        sap_pdf: UploadFile = File(...),
    ):
        identifier = _parse_identifier(
            cs_pdf.filename, bom_xlsx.filename, sap_pdf.filename
        )
        if not identifier:
            raise HTTPException(400, "Could not extract 8-digit identifier from filenames")

        upload_dir, _ = _get_dirs(identifier)
        upload_dir.mkdir(parents=True, exist_ok=True)

        files_saved = {}
        for label, upload_file in [("cs", cs_pdf), ("bom", bom_xlsx), ("sap", sap_pdf)]:
            dest = upload_dir / upload_file.filename
            with open(dest, "wb") as f:
                shutil.copyfileobj(upload_file.file, f)
            files_saved[label] = upload_file.filename
            logger.info(f"Saved {label}: {dest}")

        return {"identifier": identifier, "files": files_saved}

    @app.post("/api/extract/{identifier}")
    async def extract(identifier: str):
        upload_dir, processed_dir = _get_dirs(identifier)

        if not upload_dir.exists():
            raise HTTPException(404, f"No uploaded documents for identifier {identifier}")

        logger.info(f"Starting extraction for {identifier}")
        process_folder(upload_dir, processed=processed_dir)

        # Create aliased copies with identifier prefix
        alias_map = {
            "cs_bom.json": f"{identifier}_extracted_cs.json",
            "bom.json": f"{identifier}_extracted_bom.json",
            "sap_data.json": f"{identifier}_extracted_sap.json",
            "sap_raw.json": f"{identifier}_sap_raw.json",
            "rendered_cs_page.png": f"{identifier}_rendered_cs_page.png",
            "rendered_cs_table.png": f"{identifier}_rendered_cs_table.png",
        }
        for original, alias in alias_map.items():
            src = processed_dir / original
            if src.exists():
                shutil.copy2(src, processed_dir / alias)

        response_data = _read_results(processed_dir)
        logger.info(f"Extraction completed for {identifier}")
        return {"status": "completed", "identifier": identifier, "results": response_data}

    @app.get("/api/results/{identifier}")
    async def get_results(identifier: str):
        _, processed_dir = _get_dirs(identifier)
        if not processed_dir.exists():
            raise HTTPException(404, f"No results for identifier {identifier}")
        return _read_results(processed_dir)

    @app.get("/api/documents/{identifier}/{doc_type}")
    async def get_document(identifier: str, doc_type: str, download: bool = False):
        upload_dir, _ = _get_dirs(identifier)
        if not upload_dir.exists():
            raise HTTPException(404, f"No documents for identifier {identifier}")

        for file_path in upload_dir.iterdir():
            detected = _detect_doc_type(file_path.name)
            if detected == doc_type:
                media_type = (
                    "application/pdf" if file_path.suffix.lower() == ".pdf"
                    else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                return FileResponse(
                    path=str(file_path),
                    media_type=media_type,
                    filename=file_path.name if download else None,
                )

        raise HTTPException(404, f"No {doc_type} document found for {identifier}")

    return app


# ── Entry point ──────────────────────────────────────────────────────────────

app = create_app()

if __name__ == "__main__":
    if "--cli" in sys.argv:
        cli_args = [a for a in sys.argv[1:] if a != "--cli"]
        run_cli(cli_args)
    else:
        import uvicorn
        uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
