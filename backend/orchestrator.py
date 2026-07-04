"""
Orchestrator — coordinates the three document extractors for a single pump folder.

CSExtractor runs first (sequentially) because it has a render → LLM discovery
pipeline that must complete before results are assembled.
BOMExtractor and SAPExtractor run in parallel (they are independent).
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from backend import config
from backend.extractors import BOMExtractor, CSExtractor, SAPExtractor

logger = logging.getLogger(__name__)


def process_folder(folder: Path, processed: Path | None = None) -> dict:
    """Run all three extractors on a single document folder and return their results."""
    folder_id = folder.name
    if processed is None:
        processed = config.PROCESSED_DIR / folder_id

    processed.mkdir(parents=True, exist_ok=True)
    results = {}

    # Step 1: CS — sequential (LLM discovery stage must finish first)
    try:
        cs_ext = CSExtractor(raw_folder=folder, processed_folder=processed)
        results["CSExtractor"] = cs_ext.extract()
        logger.info(f"CSExtractor completed for {folder_id}")
    except Exception as e:
        logger.error(f"CSExtractor failed for {folder_id}: {e}")
        results["CSExtractor"] = None

    # Step 2: BOM + SAP — parallel (independent of CS)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(
                BOMExtractor(raw_folder=folder, processed_folder=processed).extract
            ): "BOMExtractor",
            pool.submit(
                SAPExtractor(raw_folder=folder, processed_folder=processed).extract
            ): "SAPExtractor",
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
                logger.info(f"{name} completed for {folder_id}")
            except Exception as e:
                logger.error(f"{name} failed for {folder_id}: {e}")
                results[name] = None

    return results


def run_cli(args: list[str]) -> None:
    """Batch-process document folders from the command line."""
    folders = (
        [config.RAW_DIR / arg for arg in args]
        if args
        else sorted(p for p in config.RAW_DIR.iterdir() if p.is_dir())
    )
    logger.info(f"Processing {len(folders)} document folder(s)")
    for folder in folders:
        if not folder.exists():
            logger.error(f"Folder not found: {folder}")
            continue
        logger.info(f"--- Processing {folder.name} ---")
        process_folder(folder)
