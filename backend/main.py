"""
BOM Components Validator — FastAPI application entry point.

Usage:
    python -m backend                   # start web server on port 8000
    python -m backend --cli             # process all folders in documents/raw/
    python -m backend --cli 81351387    # process one folder
"""

import json
import logging
import re
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend import config
from backend.orchestrator import process_folder, run_cli

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app():
    from fastapi import FastAPI, File, HTTPException, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from pydantic import BaseModel

    app = FastAPI(title="BOM Components Validator API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    IDENTIFIER_RE = re.compile(r"(\d{8})")

    # ── Directory helpers ─────────────────────────────────────────────────────

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
        data = {}
        for key, filename in [
            ("cs_bom",    "cs_bom.json"),
            ("bom_excel", "bom_data.json"),
            ("sap_data",  "sap_data.json"),
        ]:
            p = processed_dir / filename
            data[key] = json.load(open(p)) if p.exists() else None
        return data

    # ── Health ────────────────────────────────────────────────────────────────

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    # ── Upload / Extract / Results ────────────────────────────────────────────

    @app.post("/api/upload")
    async def upload_documents(
        cs_pdf:   UploadFile = File(...),
        bom_xlsx: UploadFile = File(...),
        sap_pdf:  UploadFile = File(...),
    ):
        identifier = _parse_identifier(cs_pdf.filename, bom_xlsx.filename, sap_pdf.filename)
        if not identifier:
            raise HTTPException(400, "Could not extract 8-digit identifier from filenames")

        upload_dir, _ = _get_dirs(identifier)
        upload_dir.mkdir(parents=True, exist_ok=True)

        files_saved = {}
        for label, uf in [("cs", cs_pdf), ("bom", bom_xlsx), ("sap", sap_pdf)]:
            dest = upload_dir / uf.filename
            with open(dest, "wb") as f:
                shutil.copyfileobj(uf.file, f)
            files_saved[label] = uf.filename

        return {"identifier": identifier, "files": files_saved}

    @app.post("/api/extract/{identifier}")
    async def extract(identifier: str):
        upload_dir, processed_dir = _get_dirs(identifier)
        if not upload_dir.exists():
            raise HTTPException(404, f"No uploaded documents for {identifier}")

        process_folder(upload_dir, processed=processed_dir)
        return {
            "status": "completed",
            "identifier": identifier,
            "results": _read_results(processed_dir),
        }

    @app.get("/api/results/{identifier}")
    async def get_results(identifier: str):
        _, processed_dir = _get_dirs(identifier)
        if not processed_dir.exists():
            raise HTTPException(404, f"No results for {identifier}")
        return _read_results(processed_dir)

    @app.get("/api/documents/{identifier}/{doc_type}")
    async def get_document(identifier: str, doc_type: str, download: bool = False):
        upload_dir, _ = _get_dirs(identifier)
        if not upload_dir.exists():
            raise HTTPException(404, f"No documents for {identifier}")

        for file_path in upload_dir.iterdir():
            if _detect_doc_type(file_path.name) == doc_type:
                media_type = (
                    "application/pdf"
                    if file_path.suffix.lower() == ".pdf"
                    else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                return FileResponse(
                    path=str(file_path),
                    media_type=media_type,
                    filename=file_path.name if download else None,
                )
        raise HTTPException(404, f"No {doc_type} document for {identifier}")

    # ── Compare / Validate / Report ───────────────────────────────────────────

    @app.post("/api/compare/{identifier}")
    async def compare_parts(identifier: str):
        _, processed_dir = _get_dirs(identifier)
        if not processed_dir.exists():
            raise HTTPException(404, "No extracted data. Run extraction first.")
        from backend.comparator import compare
        results = compare(identifier, processed_dir)
        return {"status": "completed", "identifier": identifier, "comparison": results}

    @app.post("/api/validate/{identifier}")
    async def validate_parts(identifier: str, body: dict):
        _, processed_dir = _get_dirs(identifier)
        if not processed_dir.exists():
            raise HTTPException(404, "No comparison results. Run comparison first.")
        from backend.comparator import apply_validation
        validation = apply_validation(identifier, processed_dir, body.get("decisions", []))
        return {"status": "completed", "identifier": identifier, "validation": validation}

    @app.get("/api/report/{identifier}")
    async def get_report(identifier: str):
        _, processed_dir = _get_dirs(identifier)
        report_path = processed_dir / f"{identifier}_validation_report.pdf"
        if not report_path.exists():
            from backend.report import generate_report
            report_path = generate_report(identifier, processed_dir)
        if not report_path.exists():
            raise HTTPException(404, "Could not generate report.")
        return FileResponse(
            path=str(report_path),
            media_type="application/pdf",
            filename=report_path.name,
        )

    # ── Nomenclature CRUD ─────────────────────────────────────────────────────

    def _nom():
        from backend.comparator import Nomenclature
        return Nomenclature()

    @app.get("/api/nomenclature")
    async def list_nomenclature():
        """Return every canonical part with its aliases and type."""
        nom = _nom()
        return {
            "parts": [
                {
                    "canonical":  name,
                    "type":       info.get("type", ""),
                    "aliases":    info.get("aliases", []),
                }
                for name, info in nom.data.items()
            ]
        }

    class AliasBody(BaseModel):
        alias: str

    class PartBody(BaseModel):
        canonical: str
        type: str = ""
        aliases: list[str] = []

    @app.post("/api/nomenclature")
    async def add_part(body: PartBody):
        """Add a new canonical part."""
        nom = _nom()
        if body.canonical in nom.data:
            raise HTTPException(409, f"'{body.canonical}' already exists")
        nom.data[body.canonical] = {
            "type": body.type,
            "aliases": [a for a in body.aliases if a.upper() != body.canonical.upper()],
        }
        nom._save()
        nom._reverse = nom._build_reverse_map()
        return {"canonical": body.canonical}

    @app.delete("/api/nomenclature/{canonical:path}")
    async def delete_part(canonical: str):
        """Delete a canonical part entirely."""
        nom = _nom()
        if canonical not in nom.data:
            raise HTTPException(404, f"'{canonical}' not found")
        del nom.data[canonical]
        nom._save()
        return {"deleted": canonical}

    @app.post("/api/nomenclature/{canonical:path}/aliases")
    async def add_alias(canonical: str, body: AliasBody):
        """Add an alias to an existing canonical part."""
        nom = _nom()
        if canonical not in nom.data:
            raise HTTPException(404, f"'{canonical}' not found")
        nom.add_alias(canonical, body.alias)
        return {"canonical": canonical, "added": body.alias}

    @app.delete("/api/nomenclature/{canonical:path}/aliases/{alias:path}")
    async def remove_alias(canonical: str, alias: str):
        """Remove a specific alias from a canonical part."""
        nom = _nom()
        if canonical not in nom.data:
            raise HTTPException(404, f"'{canonical}' not found")
        aliases = nom.data[canonical].get("aliases", [])
        if alias not in aliases:
            raise HTTPException(404, f"Alias '{alias}' not found under '{canonical}'")
        aliases.remove(alias)
        nom._save()
        return {"canonical": canonical, "removed": alias}

    @app.put("/api/nomenclature/{canonical:path}/type")
    async def update_type(canonical: str, body: dict):
        """Update the type field of a canonical part."""
        nom = _nom()
        if canonical not in nom.data:
            raise HTTPException(404, f"'{canonical}' not found")
        nom.data[canonical]["type"] = body.get("type", "")
        nom._save()
        return {"canonical": canonical, "type": nom.data[canonical]["type"]}

    return app


app = create_app()

if __name__ == "__main__":
    if "--cli" in sys.argv:
        cli_args = [a for a in sys.argv[1:] if a != "--cli"]
        run_cli(cli_args)
    else:
        import uvicorn
        uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
