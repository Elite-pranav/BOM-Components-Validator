"""
BOM Components Validator — main entry point.

Orchestrates all document extractors (CS, BOM Excel, SAP) concurrently
for each document folder under backend/documents/raw/. Results are
written to backend/documents/processed/<folder_id>/.

Usage:
    python -m backend.main                  # process all folders
    python -m backend.main 81351387         # process one folder
"""
import sys
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from backend import config
from backend.extractors import CSExtractor, BOMExtractor, SAPExtractor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

EXTRACTORS = [CSExtractor, BOMExtractor, SAPExtractor]


def process_folder(folder: Path):
    """Run all extractors on a single document folder."""
    folder_id = folder.name
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


def main():
    if len(sys.argv) > 1:
        folders = [config.RAW_DIR / arg for arg in sys.argv[1:]]
    else:
        folders = sorted(p for p in config.RAW_DIR.iterdir() if p.is_dir())

    logger.info(f"Processing {len(folders)} document folder(s)")

    for folder in folders:
        if not folder.exists():
            logger.error(f"Folder not found: {folder}")
            continue
        logger.info(f"--- Processing {folder.name} ---")
        process_folder(folder)


if __name__ == "__main__":
    main()
