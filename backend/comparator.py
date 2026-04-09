"""
Part comparison engine for the BOM Components Validator.

Normalizes part names across CS, BOM Excel, and SAP documents using a
global nomenclature file, then cross-references presence and materials
to flag discrepancies (MISSING or MATERIAL_MISMATCH).

The Nomenclature class manages the ever-growing alias map. When users
reject a false-positive discrepancy, the alias that caused it is added
so future comparisons resolve correctly.
"""

import json
import logging
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from backend import config

logger = logging.getLogger(__name__)

NOMENCLATURE_PATH = config.BACKEND_DIR / "nomenclature.json"


class Nomenclature:
    """Loads, queries, and updates the global part nomenclature."""

    def __init__(self, path: Path | None = None):
        self.path = path or NOMENCLATURE_PATH
        self.data = self._load()
        self._reverse = self._build_reverse_map()

    def _load(self) -> dict:
        if self.path.exists():
            with open(self.path) as f:
                return json.load(f)
        return {}

    def _save(self):
        """Atomic write: write to temp file then rename."""
        content = json.dumps(self.data, indent=2, ensure_ascii=False)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            f.write(content)
        tmp.replace(self.path)

    def _build_reverse_map(self) -> dict[str, str]:
        """Build {ALIAS_UPPER: canonical_name} lookup."""
        rev = {}
        for canonical, info in self.data.items():
            # The canonical name itself is also a valid alias
            rev[canonical.upper()] = canonical
            for alias in info.get("aliases", []):
                rev[alias.upper()] = canonical
        return rev

    def resolve(self, name: str) -> str | None:
        """Resolve a part name to its canonical form. Case-insensitive."""
        if not name:
            return None
        return self._reverse.get(name.strip().upper())

    def add_alias(self, canonical: str, new_alias: str):
        """Add a new alias for an existing canonical part and persist."""
        if canonical not in self.data:
            self.data[canonical] = {"aliases": []}

        aliases = self.data[canonical]["aliases"]
        if new_alias not in aliases:
            aliases.append(new_alias)
            self._reverse[new_alias.upper()] = canonical
            self._save()
            logger.info(f"Added alias '{new_alias}' → '{canonical}'")

    def get_all_canonical(self) -> list[str]:
        """Return sorted list of all canonical part names."""
        return sorted(self.data.keys())


# ── Comparison Logic ─────────────────────────────────────────────────────────

def compare(identifier: str, processed_dir: Path) -> dict:
    """
    Compare parts across CS, BOM, and SAP extracted data.

    Returns a comparison results dict with per-part presence/material info
    and flagged discrepancies.
    """
    nomenclature = Nomenclature()

    cs_data = _load_json(processed_dir / "cs_bom.json", default=[])
    bom_data = _load_json(processed_dir / "bom.json", default=[])
    sap_data = _load_json(processed_dir / "sap_data.json", default={})

    # Normalize each document's parts into {canonical_name: {material, qty, ...}}
    cs_parts, cs_unresolved = _normalize_cs(cs_data, nomenclature)
    bom_parts, bom_unresolved = _normalize_bom(bom_data, nomenclature)
    sap_parts, sap_unresolved = _normalize_sap(sap_data, nomenclature)

    # Union of all canonical names found
    all_canonical = sorted(
        set(cs_parts.keys()) | set(bom_parts.keys()) | set(sap_parts.keys())
    )

    # Build comparison for each canonical part
    parts_comparison = []
    for canonical in all_canonical:
        cs_entry = cs_parts.get(canonical)
        bom_entry = bom_parts.get(canonical)
        sap_entry = sap_parts.get(canonical)

        discrepancies = _find_discrepancies(canonical, cs_entry, bom_entry, sap_entry)

        parts_comparison.append({
            "canonical_name": canonical,
            "cs": cs_entry,
            "bom": bom_entry,
            "sap": sap_entry,
            "discrepancies": discrepancies,
        })

    # Combine unresolved from all sources
    unresolved = cs_unresolved + bom_unresolved + sap_unresolved

    total_discrepancies = sum(
        len(p["discrepancies"]) for p in parts_comparison
    )

    results = {
        "identifier": identifier,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_canonical_parts": len(all_canonical),
            "discrepancies_found": total_discrepancies,
            "unresolved_parts": len(unresolved),
        },
        "parts": parts_comparison,
        "unresolved": unresolved,
    }

    # Save to processed folder
    output_path = processed_dir / "comparison_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(
        f"Comparison complete for {identifier}: "
        f"{len(all_canonical)} parts, {total_discrepancies} discrepancies, "
        f"{len(unresolved)} unresolved"
    )

    return results


# ── Normalization helpers ────────────────────────────────────────────────────

def _normalize_cs(cs_data: list, nom: Nomenclature) -> tuple[dict, list]:
    """Normalize CS BOM entries. Returns (resolved_parts, unresolved_list)."""
    parts = {}
    unresolved = []

    for entry in cs_data:
        desc = (entry.get("description") or "").strip()
        if not desc:
            continue

        # Skip header row if accidentally included
        if desc.upper() in ("DESCRIPTION", "REF.", "MATERIAL."):
            continue

        # Skip generic entries like fasteners
        if _is_fastener_or_generic(desc):
            continue

        canonical = nom.resolve(desc)
        if not canonical:
            # Try resolving just the first meaningful words
            canonical = _try_partial_resolve(desc, nom)

        if canonical:
            # If multiple CS entries map to same canonical, keep all materials
            if canonical not in parts:
                parts[canonical] = {
                    "present": True,
                    "material": entry.get("material"),
                    "qty": entry.get("qty"),
                }
            # If already present, could be variant (e.g., DIFFUSER STAGE + DLY)
        else:
            unresolved.append({
                "source": "cs",
                "original_name": desc,
                "ref": entry.get("ref"),
            })

    return parts, unresolved


def _normalize_bom(bom_data: list, nom: Nomenclature) -> tuple[dict, list]:
    """Normalize BOM Excel entries. Returns (resolved_parts, unresolved_list)."""
    parts = {}
    unresolved = []

    for entry in bom_data:
        part_type = entry.get("part_type")
        desc = (entry.get("description") or "").strip()

        # Skip entries without a part type (fasteners, hardware, etc.)
        if not part_type:
            continue

        canonical = nom.resolve(part_type)
        if not canonical:
            canonical = nom.resolve(desc)
        if not canonical:
            canonical = _try_partial_resolve(desc, nom)

        if canonical:
            if canonical not in parts:
                parts[canonical] = {
                    "present": True,
                    "material": entry.get("material"),
                    "qty": entry.get("quantity"),
                    "coating": entry.get("coating"),
                }
        else:
            unresolved.append({
                "source": "bom",
                "original_name": part_type or desc,
                "item_number": entry.get("item_number"),
            })

    return parts, unresolved


def _normalize_sap(sap_data: dict, nom: Nomenclature) -> tuple[dict, list]:
    """Normalize SAP data entries. Returns (resolved_parts, unresolved_list)."""
    parts = {}
    unresolved = []
    sap_parts = sap_data.get("parts", {})

    for name, info in sap_parts.items():
        canonical = nom.resolve(name)

        if canonical:
            parts[canonical] = {
                "present": True,
                "material": info.get("material"),
                "raw_material": info.get("raw"),
                "coating": info.get("coating"),
            }
        else:
            unresolved.append({
                "source": "sap",
                "original_name": name,
            })

    return parts, unresolved


# ── Discrepancy detection ────────────────────────────────────────────────────

def _find_discrepancies(
    canonical: str,
    cs: dict | None,
    bom: dict | None,
    sap: dict | None,
) -> list[dict]:
    """Find MISSING and MATERIAL_MISMATCH discrepancies for a canonical part."""
    discrepancies = []

    # Check presence
    sources = {"cs": cs, "bom": bom, "sap": sap}
    present_in = [name for name, data in sources.items() if data]
    missing_from = [name for name, data in sources.items() if not data]

    if missing_from and present_in:
        discrepancies.append({
            "type": "MISSING",
            "detail": f"Present in {', '.join(present_in)} but missing from {', '.join(missing_from)}",
            "present_in": present_in,
            "missing_from": missing_from,
        })

    # Check material consistency (only between sources that have the part)
    materials = {}
    for name, data in sources.items():
        if data and data.get("material"):
            materials[name] = _normalize_material(data["material"])

    if len(materials) >= 2:
        unique_materials = set(materials.values())
        if len(unique_materials) > 1:
            detail_parts = [f"{src}: {mat}" for src, mat in materials.items()]
            discrepancies.append({
                "type": "MATERIAL_MISMATCH",
                "detail": f"Material differs — {', '.join(detail_parts)}",
                "materials": materials,
            })

    return discrepancies


def _normalize_material(material: str) -> str:
    """Normalize material string for comparison.

    Strips common prefixes (ASTM, IS, GR, etc.), whitespace, and
    standardizes format so 'ASTM A276 GR SS410' and 'SS410' match.
    """
    if not material:
        return ""
    upper = material.upper().strip()
    # Extract the core material code
    # Try common patterns in order of specificity
    patterns = [
        r"(CA\d+\w*)",
        r"(CF\d+\w*)",
        r"(SS\d{3}\w?)",
        r"(GGG\d+)",
        r"(FG\s?\d+)",
        r"(EN\d+\w*)",
        r"(WCB)",
        r"(LTB\d+)",
        r"\b(HTS)\b",
        r"\b(CI)\b",
        r"\b(MS)\b",
        r"(CUT(?:LESS)?\s*RUBBER)",
        r"(NITRILE)",
        r"(COPPER)",
    ]
    for pat in patterns:
        m = re.search(pat, upper)
        if m:
            core = m.group(1).strip()
            # Append coating info
            if "COAT" in upper:
                core += " + COATING"
            return core

    return upper


# ── Utility helpers ──────────────────────────────────────────────────────────

def _is_fastener_or_generic(desc: str) -> bool:
    """Check if a description refers to a generic fastener/hardware item."""
    upper = desc.upper()
    fastener_keywords = [
        "FASTNER", "FASTENER", "GASKET", "O' RING", "O RING",
        "'O' RING", "WASHER", "STUD", "HEX NUT", "HEX HD SCR",
        "SOC SET SCR", "SOC HD CAP", "PEG PIN", "HEX PLUG",
    ]
    return any(kw in upper for kw in fastener_keywords)


def _try_partial_resolve(desc: str, nom: Nomenclature) -> str | None:
    """Try resolving by progressively shorter prefixes of the description."""
    words = desc.split()
    # Try first 4 words, then 3, then 2
    for n in range(min(4, len(words)), 1, -1):
        prefix = " ".join(words[:n])
        result = nom.resolve(prefix)
        if result:
            return result
    return None


def _load_json(path: Path, default=None):
    """Load a JSON file, returning default if not found."""
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


# ── Validation ───────────────────────────────────────────────────────────────

def apply_validation(identifier: str, processed_dir: Path, decisions: list[dict]) -> dict:
    """
    Apply user validation decisions to comparison results.

    For 'agree' decisions: mark discrepancy as confirmed.
    For 'disagree' decisions: update nomenclature with new alias mapping.

    Returns updated comparison results.
    """
    results = _load_json(processed_dir / "comparison_results.json", default={})
    nomenclature = Nomenclature()

    confirmed = []
    dismissed = []

    for decision in decisions:
        canonical = decision["canonical_name"]
        action = decision["action"]
        disc_index = decision.get("discrepancy_index", 0)

        if action == "agree":
            confirmed.append({
                "canonical_name": canonical,
                "discrepancy_index": disc_index,
            })
        elif action == "disagree":
            mapped = decision.get("mapped_canonical")
            original_name = decision.get("original_name")
            if mapped and original_name:
                nomenclature.add_alias(mapped, original_name)
            dismissed.append({
                "canonical_name": canonical,
                "discrepancy_index": disc_index,
                "mapped_to": mapped,
            })

    validation_status = {
        "identifier": identifier,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confirmed_discrepancies": confirmed,
        "dismissed_discrepancies": dismissed,
        "total_confirmed": len(confirmed),
        "total_dismissed": len(dismissed),
    }

    # Save validation status
    with open(processed_dir / "validation_status.json", "w") as f:
        json.dump(validation_status, f, indent=2)

    logger.info(
        f"Validation for {identifier}: "
        f"{len(confirmed)} confirmed, {len(dismissed)} dismissed"
    )

    return validation_status
