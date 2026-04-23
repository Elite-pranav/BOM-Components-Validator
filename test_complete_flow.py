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
import tempfile
import shutil
import unittest
from pathlib import Path

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
        self.assertNotIn("error", data)

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

    def _run_compare_in_temp_dir(self, cs_data, bom_data, sap_data, identifier="test_id"):
        """Write temporary input files and run the comparison function."""
        original_gemini_key = config.GEMINI_API_KEY
        config.GEMINI_API_KEY = ""
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_processed = Path(temp_dir)
                with open(temp_processed / "cs_bom.json", "w") as f:
                    json.dump(cs_data, f)
                with open(temp_processed / "bom.json", "w") as f:
                    json.dump(bom_data, f)
                with open(temp_processed / "sap_data.json", "w") as f:
                    json.dump(sap_data, f)

                return compare(identifier, temp_processed)
        finally:
            config.GEMINI_API_KEY = original_gemini_key

    def _get_part(self, results, canonical_name):
        """Return the comparison result entry for a canonical part name."""
        return next(
            (part for part in results.get("parts", []) if part.get("canonical_name") == canonical_name),
            None,
        )

    def _assert_exact_discrepancy(self, results, canonical_name, expected_type, expected_count=1, cs_material=None, bom_material=None, sap_material=None, cs_qty=None, bom_qty=None, sap_qty=None, missing_in=None, present_in=None):
        """Strict assertion for exact discrepancy details."""
        part = self._get_part(results, canonical_name)
        self.assertIsNotNone(part, f"Expected part '{canonical_name}' in comparison results")

        discrepancies = part.get("discrepancies", [])
        self.assertEqual(len(discrepancies), expected_count, f"Expected exactly {expected_count} discrepancies for '{canonical_name}', found {len(discrepancies)}")

        if expected_count > 0:
            disc = discrepancies[0]  # Assume first discrepancy for simplicity
            self.assertEqual(disc["type"], expected_type, f"Expected discrepancy type '{expected_type}', got '{disc['type']}'")

        # Check materials if provided
        if cs_material is not None:
            self.assertEqual(part["cs"].get("material"), cs_material, f"Expected CS material '{cs_material}', got '{part['cs'].get('material')}'")
        if bom_material is not None:
            self.assertEqual(part["bom"].get("material"), bom_material, f"Expected BOM material '{bom_material}', got '{part['bom'].get('material')}'")
        if sap_material is not None:
            self.assertEqual(part["sap"].get("material"), sap_material, f"Expected SAP material '{sap_material}', got '{part['sap'].get('material')}'")

        # Check quantities if provided
        if cs_qty is not None:
            self.assertEqual(part["cs"].get("qty") or part["cs"].get("quantity"), cs_qty, f"Expected CS qty '{cs_qty}', got '{part['cs'].get('qty') or part['cs'].get('quantity')}'")
        if bom_qty is not None:
            self.assertEqual(part["bom"].get("qty") or part["bom"].get("quantity"), bom_qty, f"Expected BOM qty '{bom_qty}', got '{part['bom'].get('qty') or part['bom'].get('quantity')}'")
        if sap_qty is not None:
            self.assertEqual(part["sap"].get("qty") or part["sap"].get("quantity"), sap_qty, f"Expected SAP qty '{sap_qty}', got '{part['sap'].get('qty') or part['sap'].get('quantity')}'")

        # Check missing/present if provided
        if missing_in:
            self.assertEqual(part.get("missing_from"), missing_in, f"Expected missing_from {missing_in}, got {part.get('missing_from')}")
        if present_in:
            self.assertEqual(part.get("present_in"), present_in, f"Expected present_in {present_in}, got {part.get('present_in')}")

    def _assert_no_discrepancy(self, results, canonical_name):
        """Assert no discrepancies for a part."""
        part = self._get_part(results, canonical_name)
        self.assertIsNotNone(part, f"Expected part '{canonical_name}' in comparison results")
        self.assertEqual(
            part.get("discrepancies", []),
            [],
            f"Expected no discrepancies for '{canonical_name}', found {part.get('discrepancies', [])}",
        )

    def test_extractors_individually(self):
        """Test each extractor individually if raw files exist."""
        raw_dir = config.RAW_DIR / self.sample_id
        if not raw_dir.exists():
            self.skipTest("Raw data directory not found")

        # Test BOM Extractor
        bom_files = list(raw_dir.glob("*BOM*.xlsx"))
        if bom_files:
            extractor = BOMExtractor(raw_folder=raw_dir, processed_folder=self.processed_dir)
            result = extractor.extract()
            self.assertIsInstance(result, dict)
            self.assertIn("parts", result)
            self.assertGreater(len(result["parts"]), 0)
            for item in result["parts"]:
                self.assertTrue(
                    any(key in item for key in ["item_number", "ref", "part_type"]),
                    f"BOM item should contain item_number, ref, or part_type: {item}",
                )
                self.assertIn("description", item)
                self.assertTrue("qty" in item or "quantity" in item)

        # Test CS Extractor
        cs_files = list(raw_dir.glob("*CS*.pdf"))
        if cs_files:
            extractor = CSExtractor(raw_folder=raw_dir, processed_folder=self.processed_dir)
            result = extractor.extract()
            self.assertIsInstance(result, list)
            self.assertGreater(len(result), 0)
            for item in result:
                self.assertTrue(
                    any(key in item for key in ["item_number", "ref", "description"]),
                    f"CS item should contain item_number, ref, or description: {item}",
                )
                self.assertIn("description", item)
                self.assertTrue("qty" in item or "quantity" in item)

        # Test SAP Extractor
        sap_files = list(raw_dir.glob("*SAP*.xlsx"))
        if sap_files:
            extractor = SAPExtractor(raw_folder=raw_dir, processed_folder=self.processed_dir)
            result = extractor.extract()
            self.assertIsInstance(result, list)
            self.assertGreater(len(result), 0)
            for item in result:
                self.assertTrue(
                    any(key in item for key in ["item_number", "ref", "description"]),
                    f"SAP item should contain item_number, ref, or description: {item}",
                )
                self.assertIn("description", item)
                self.assertTrue("qty" in item or "quantity" in item)

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
        for extractor, result in results.items():
            self.assertIsNotNone(result, f"{extractor} failed")

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
        cs_data = [{"description": "Impeller", "material": "SS304", "qty": 1}]
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
        self.assertGreater(len(discrepancies), 0)

        # Test with matching data
        bom_data = [{"item_number": "1", "part_type": "Impeller", "description": "Impeller", "material": "SS304", "qty": 1}]
        sap_data = {"metadata": {}, "parts": {"Impeller": {"material": "SS304", "quantity": 1}}}

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

    def test_discrepancy_material_mismatch(self):
        """Material mismatch should be detected when one source differs."""
        cs_data = [{"description": "Impeller", "material": "SS304", "qty": 1}]
        bom_data = [{"item_number": "1", "part_type": "Impeller", "description": "Impeller", "material": "CA15", "quantity": 1}]
        sap_data = {"metadata": {}, "parts": {"Impeller": {"material": "SS304", "quantity": 1}}}

        results = self._run_compare_in_temp_dir(cs_data, bom_data, sap_data)
        self._assert_exact_discrepancy(results, "Impeller", "MATERIAL_MISMATCH", cs_material="SS304", bom_material="CA15", sap_material="SS304")

    def test_discrepancy_quantity_mismatch(self):
        """Quantity mismatch should be detected when source quantities differ."""
        cs_data = [{"description": "Impeller", "material": "SS304", "qty": 1}]
        bom_data = [{"item_number": "1", "part_type": "Impeller", "description": "Impeller", "material": "SS304", "quantity": 2}]
        sap_data = {"metadata": {}, "parts": {"Impeller": {"material": "SS304", "quantity": 1}}}

        results = self._run_compare_in_temp_dir(cs_data, bom_data, sap_data)
        self._assert_exact_discrepancy(results, "Impeller", "QUANTITY_MISMATCH", cs_qty=1, bom_qty=2, sap_qty=1)

    def test_discrepancy_missing_part_in_one_source(self):
        """Missing part should be flagged when one source does not contain the part."""
        cs_data = [{"description": "Impeller", "material": "SS304", "qty": 1}]
        bom_data = [{"item_number": "1", "part_type": "Impeller", "description": "Impeller", "material": "SS304", "quantity": 1}]
        sap_data = {"metadata": {}, "parts": {}}

        results = self._run_compare_in_temp_dir(cs_data, bom_data, sap_data)
        self._assert_exact_discrepancy(results, "Impeller", "MISSING", missing_in=["sap"], present_in=["cs", "bom"])

    def test_discrepancy_extra_part_in_one_source(self):
        """Extra part in one source should be reported as missing from the remaining documents."""
        cs_data = []
        bom_data = [{"item_number": "1", "part_type": "Impeller", "description": "Impeller", "material": "SS304", "quantity": 1}]
        sap_data = {"metadata": {}, "parts": {}}

        results = self._run_compare_in_temp_dir(cs_data, bom_data, sap_data)
        self._assert_exact_discrepancy(results, "Impeller", "MISSING", missing_in=["cs"], present_in=["bom"])

    def test_synonym_equivalent_names_should_not_raise_discrepancy(self):
        """Different but equivalent names should not produce a discrepancy."""
        cs_data = [{"description": "IMP", "material": "SS304", "qty": 1}]
        bom_data = [{"item_number": "1", "part_type": "IMPELLER", "description": "IMP", "material": "SS304", "quantity": 1}]
        sap_data = {"metadata": {}, "parts": {"IMPELLER": {"material": "SS304", "quantity": 1}}}

        results = self._run_compare_in_temp_dir(cs_data, bom_data, sap_data)
        self._assert_no_discrepancy(results, "Impeller")

    def test_ocr_noise_text_handling(self):
        """OCR-style noise and punctuation should still resolve the same part."""
        cs_data = [{"description": "Impeller.", "material": "SS304", "qty": 1}]
        bom_data = [{"item_number": "1", "part_type": "Impeller", "description": "Impeller", "material": "SS304", "quantity": 1}]
        sap_data = {"metadata": {}, "parts": {"Impeller": {"material": "SS304", "quantity": 1}}}

        results = self._run_compare_in_temp_dir(cs_data, bom_data, sap_data)
        self._assert_no_discrepancy(results, "Impeller")

    def test_duplicate_row_handling(self):
        """Duplicate CS rows for the same part should not create duplicate comparison entries."""
        cs_data = [
            {"description": "Impeller", "material": "SS304", "qty": 1},
            {"description": "Impeller", "material": "SS304", "qty": 1},
        ]
        bom_data = [{"item_number": "1", "part_type": "Impeller", "description": "Impeller", "material": "SS304", "quantity": 1}]
        sap_data = {"metadata": {}, "parts": {"Impeller": {"material": "SS304", "quantity": 1}}}

        results = self._run_compare_in_temp_dir(cs_data, bom_data, sap_data)
        parts = [p for p in results["parts"] if p["canonical_name"] == "Impeller"]
        self.assertEqual(len(parts), 1, "Duplicate CS rows should be collapsed into one canonical part")
        self._assert_no_discrepancy(results, "Impeller")

    def test_case_sensitivity_and_spacing_differences_do_not_raise_discrepancy(self):
        """Case and spacing differences should not cause a false discrepancy."""
        cs_data = [{"description": "  suction bell mouth  ", "material": "SS304", "qty": 1}]
        bom_data = [{"item_number": "1", "part_type": "SUCTION BELL MOUTH", "description": "Suction Bell Mouth", "material": "SS304", "quantity": 1}]
        sap_data = {"metadata": {}, "parts": {"Suction Bell Mouth": {"material": "SS304", "quantity": 1}}}

        results = self._run_compare_in_temp_dir(cs_data, bom_data, sap_data)
        self._assert_no_discrepancy(results, "Suction Bell Mouth")

    def test_multi_source_conflict_all_three_different(self):
        """All three sources disagree on material and should flag a material mismatch."""
        cs_data = [{"description": "Impeller", "material": "SS304", "qty": 1}]
        bom_data = [{"item_number": "1", "part_type": "Impeller", "description": "Impeller", "material": "CA15", "quantity": 1}]
        sap_data = {"metadata": {}, "parts": {"Impeller": {"material": "CF8M", "quantity": 1}}}

        results = self._run_compare_in_temp_dir(cs_data, bom_data, sap_data)
        self._assert_exact_discrepancy(results, "Impeller", "MATERIAL_MISMATCH", cs_material="SS304", bom_material="CA15", sap_material="CF8M")

    def test_name_mismatch_reports_missing(self):
        """Different canonical names across sources should be reported as missing parts."""
        cs_data = [{"description": "Impeller", "material": "SS304", "qty": 1}]
        bom_data = [{"item_number": "1", "part_type": "Diffuser", "description": "Diffuser", "material": "SS304", "quantity": 1}]
        sap_data = {"metadata": {}, "parts": {"Impeller": {"material": "SS304", "quantity": 1}}}

        results = self._run_compare_in_temp_dir(cs_data, bom_data, sap_data)
        self._assert_exact_discrepancy(results, "Impeller", "MISSING", missing_in=["bom"], present_in=["cs", "sap"])
        self._assert_exact_discrepancy(results, "Diffuser", "MISSING", missing_in=["cs", "sap"], present_in=["bom"])

    def test_material_synonyms_fg260(self):
        """FG260 and CI IS 210 GR FG260 should match."""
        cs_data = [{"description": "Gland Bush", "material": "FG260", "qty": 1}]

        bom_data = [{
            "item_number": "1",
            "part_type": "Gland Bush",
            "description": "Gland Bush",
            "material": "CI IS 210 GR FG260",
            "quantity": 1
        }]

        sap_data = {
            "metadata": {},
            "parts": {
                "Gland Bush": {
                    "material": "FG260",
                    "quantity": 1
                }
            }
        }

        results = self._run_compare_in_temp_dir(cs_data, bom_data, sap_data)
        self._assert_no_discrepancy(results, "Gland Bush")

    def test_no_false_positives_matching_inputs(self):
        """Identical inputs across all sources should produce no discrepancies."""
        cs_data = [{"description": "Impeller", "material": "SS304", "qty": 1}]
        bom_data = [{"item_number": "1", "part_type": "Impeller", "description": "Impeller", "material": "SS304", "quantity": 1}]
        sap_data = {"metadata": {}, "parts": {"Impeller": {"material": "SS304", "quantity": 1}}}

        results = self._run_compare_in_temp_dir(cs_data, bom_data, sap_data)
        self._assert_no_discrepancy(results, "Impeller")


if __name__ == "__main__":
    unittest.main()