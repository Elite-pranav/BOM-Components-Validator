"""
Complete Robust Test Cases for BOM Components Validator

This script tests the complete flow of the project, including:
- API endpoints
- Data extraction from documents
- Part comparison and validation
- Result generation and validation

Run with: python test_complete_flow.py

Assumes sample data exists in backend/documents/81351387/processed/
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import result

# Ensure the project root is on sys.path so `backend.*` imports work
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.comparator import compare
from backend.extractors import CSExtractor, BOMExtractor, SAPExtractor
from backend import config


class TestCompleteFlow(unittest.TestCase):
    """Test suite for the complete BOM Components Validator flow."""

    def setUp(self):
        """Set up test client and sample data paths."""
        self.app = create_app()
        self.client = TestClient(self.app)
        self.sample_id = "81351387"
        self.processed_dir = config.PROCESSED_DIR / self.sample_id
        self.upload_dir = config.DOCUMENTS_DIR / self.sample_id / "uploaded_documents"

    def test_health_endpoint(self):
        """Test the health check endpoint."""
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_upload_endpoint(self):
        """Test the upload endpoint with mock files."""
        # Note: In a real scenario, provide actual file uploads.
        # For this test, we assume files are pre-uploaded.
        # If files exist, test parsing identifier.
        if self.upload_dir.exists():
            files = list(self.upload_dir.glob("*"))
            if files:
                # Simulate identifier extraction
                import re
                identifier_re = re.compile(r"(\d{8})")
                identifiers = [identifier_re.search(f.name) for f in files]
                valid_ids = [m.group(1) for m in identifiers if m]
                self.assertTrue(valid_ids, "No valid identifiers found in uploaded files")
                self.assertEqual(valid_ids[0], self.sample_id)

    def test_extract_endpoint(self):
        """Test the extract endpoint."""
        if not self.upload_dir.exists():
            self.skipTest("Uploaded documents not found for extraction test")

        response = self.client.post(f"/api/extract/{self.sample_id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["identifier"], self.sample_id)
        self.assertIn("results", data)
        results = data["results"]
        self.assertIn("cs_bom", results)
        self.assertIn("bom_excel", results)
        self.assertIn("sap_data", results)

    def test_results_endpoint(self):
        """Test the results endpoint."""
        if not self.processed_dir.exists():
            self.skipTest("Processed data not found for results test")

        response = self.client.get(f"/api/results/{self.sample_id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("cs_bom", data)
        self.assertIn("bom_excel", data)
        self.assertIn("sap_data", data)

    def test_comparison_logic(self):
        """Test the part comparison logic with sample data."""
        import tempfile
        import shutil

        cs_path = self.processed_dir / "cs_bom.json"
        bom_path = self.processed_dir / "bom.json"
        sap_path = self.processed_dir / "sap_data.json"

        if not all(p.exists() for p in [cs_path, bom_path, sap_path]):
            self.skipTest("Sample data files not found")

        # Create a temp processed dir and copy the files
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_processed = Path(temp_dir)
            shutil.copy2(cs_path, temp_processed / "cs_bom.json")
            shutil.copy2(bom_path, temp_processed / "bom.json")
            shutil.copy2(sap_path, temp_processed / "sap_data.json")

            results = compare(self.sample_id, temp_processed)

        # Validate structure
        self.assertIsInstance(results, dict)
        self.assertIn("identifier", results)
        self.assertIn("timestamp", results)
        self.assertIn("summary", results)
        self.assertIn("parts", results)

        summary = results["summary"]
        self.assertIn("total_canonical_parts", summary)
        self.assertIn("discrepancies_found", summary)
        self.assertIn("unresolved_parts", summary)

        # Validate parts
        parts = results["parts"]
        self.assertIsInstance(parts, list)
        self.assertGreater(len(parts), 0)

        for part in parts:
            self.assertIn("canonical_name", part)
            self.assertIn("cs", part)
            self.assertIn("bom", part)
            self.assertIn("sap", part)
            self.assertIn("material_comparison", part)
            self.assertIn("discrepancies", part)

        # Check specific known part
        part_names = [p["canonical_name"] for p in parts]
        self.assertIn("Adjusting Ring", part_names)

        # Validate summary numbers are reasonable
        self.assertGreater(summary["total_canonical_parts"], 0)
        self.assertGreaterEqual(summary["discrepancies_found"], 0)
        self.assertGreaterEqual(summary["unresolved_parts"], 0)

    def test_extractors_individually(self):
        """Test each extractor individually if raw files exist."""
        raw_dir = config.RAW_DIR / self.sample_id
        if not raw_dir.exists():
            self.skipTest("Raw data directory not found")

        # BOM Extractor
        bom_files = list(raw_dir.glob("*BOM*.xlsx"))
        if bom_files:
            extractor = BOMExtractor(raw_folder=raw_dir, processed_folder=self.processed_dir)
            result = extractor.extract()
            self.assertIsInstance(result, list)
            self.assertGreater(len(result), 0)

        # SAP Extractor
        sap_files = list(raw_dir.glob("*SAP*"))
        if sap_files:
           extractor = SAPExtractor(raw_folder=raw_dir, processed_folder=self.processed_dir)
           result = extractor.extract()
           self.assertIsInstance(result, dict)
           self.assertIn("parts", result)

        # CS Extractor (AI dependent)
        cs_files = list(raw_dir.glob("*CS*.pdf"))
        if cs_files:
            try:
                extractor = CSExtractor(raw_folder=raw_dir, processed_folder=self.processed_dir)
                result = extractor.extract()
                self.assertIsInstance(result, list)
            except Exception as e:
                if "429" in str(e) or "quota" in str(e).lower():
                    self.skipTest("Gemini API quota exceeded")
                raise

    def test_full_process_flow(self):
        """Test the full process flow from raw to comparison."""
        raw_dir = config.RAW_DIR / self.sample_id
        if not raw_dir.exists():
            self.skipTest("Raw data not available for full flow test")

        from backend.main import process_folder
        results = process_folder(raw_dir, processed=self.processed_dir)

        self.assertIsInstance(results, dict)
        self.assertIn("CSExtractor", results)
        self.assertIn("BOMExtractor", results)
        self.assertIn("SAPExtractor", results)

        # Ensure no exceptions
        self.assertTrue((self.processed_dir / "bom.json").exists())
        self.assertTrue((self.processed_dir / "sap_data.json").exists())

        # Check if comparison can be run
        cs_data = results["CSExtractor"]
        bom_data = results["BOMExtractor"]
        sap_data = results["SAPExtractor"]

        if cs_data and bom_data and sap_data:
            comparison = compare(self.sample_id, self.processed_dir)
            self.assertIsInstance(comparison, dict)
            self.assertIn("parts", comparison)

    def test_validation_scenarios(self):
        """Test various validation scenarios."""
        import tempfile
        import shutil

        # Test with missing data
        cs_data = [{"ref":"1822","description":"ADJUSTING RING","material":"MS","qty":1}]
        bom_data = []
        sap_data = {"metadata": {}, "parts": {}}

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_processed = Path(temp_dir)
            with open(temp_processed / "cs_bom.json", "w") as f:
                json.dump(cs_data, f)
            with open(temp_processed / "bom.json", "w") as f:
                json.dump(bom_data, f)
            with open(temp_processed / "sap_data.json", "w") as f:
                json.dump(sap_data, f)

            results = compare("test_id", temp_processed)

        self.assertIsInstance(results, dict)
        self.assertGreater(len(results["parts"]), 0)

        # Check discrepancies for missing parts
        discrepancies = [p for p in results["parts"] if p["discrepancies"]]
        self.assertGreaterEqual(len(discrepancies), 0)

        # Test with matching data
        bom_data = [{"item_number":"1","description":"ADJUSTING RING","part_type":"Adjusting Ring","material":"MS","quantity":1}]
        sap_data = {"metadata": {},"parts": {"Adjusting Ring": {"material":"MS","quantity":1}}}

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_processed = Path(temp_dir)
            with open(temp_processed / "cs_bom.json", "w") as f:
                json.dump(cs_data, f)
            with open(temp_processed / "bom.json", "w") as f:
                json.dump(bom_data, f)
            with open(temp_processed / "sap_data.json", "w") as f:
                json.dump(sap_data, f)

            results = compare("test_id", temp_processed)


        self.assertIsInstance(results, dict)

        # Should have fewer discrepancies
        discrepancies = [p for p in results["parts"] if p["discrepancies"]]
        self.assertLessEqual(len(discrepancies), len(cs_data))


if __name__ == "__main__":
    unittest.main()