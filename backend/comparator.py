"""
Part comparison engine for the BOM Components Validator.

Architecture — two-pass design:

  Pass 1 (Rigid): Deterministic, rule-based.
    - Uses semantic family equivalence from materials.py (SS410T = SS410, CA15 = SS410).
    - Applies the authority model: SAP > CS > BOM for material truth.
    - Applies coating resolution: if SAP metadata says coating required, BOM
      coating flag is expected and never causes a mismatch.
    - Applies CS sanity check: if CS assigns a consumable material to a
      structural part, that CS value is excluded from comparison and a
      WARNING is added instead.
    - Absence rules: SAP absent = always OK. BOM absent = OK for non-major parts.
    - Quantity comparison is intentionally skipped: BOM sub-variants make
      quantity comparison unreliable at this stage.
    - Goal: clear ~90% of parts deterministically, with consistent results
      on every run for the same input data.

  Pass 2 (LLM): Only genuine alloy family conflicts.
    - Only parts where two or more sources have material data AND their
      resolved families are genuinely different reach this pass.
    - Estimated: 2-5 parts per pump (vs ~15 in previous version).
    - Uses Gemini free tier — small batch, well within rate limits.

Authority model:
  SAP  — authoritative for material of ~20 key wetted parts.
          Absence from SAP is always expected and never flagged.
  CS   — authoritative for part identity and presence of structural parts.
          Material is ground truth when SAP is absent.
          CS material is excluded if it looks like a consumable on a structural part
          (extraction error indicator).
  BOM  — authoritative for quantities and sub-variants.
          Material is abbreviated and secondary; used for confirmation only.
          Absence from BOM is OK for non-major parts.

Report guidance stored per part:
  When a mismatch is found, the "authority" field indicates which document
  holds the correct value, enabling the report to say e.g.:
  "BOM appears incorrect — CS and SAP both say CI FG260."
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import google.generativeai as genai

from backend import config
from backend.materials import (
    get_material_family,
    is_consumable_material,
    is_coating_brand,
    load_part_type_sets,
    normalize_for_rigid_comparison,
    rigid_materials_match,
    MAJOR_WETTED_PARTS,
    STRUCTURAL_PART_NAMES,
)

logger = logging.getLogger(__name__)

NOMENCLATURE_PATH = config.BACKEND_DIR / "nomenclature.json"

# ── BOM description: part prefix abbreviations (longest first) ──────────────

_BOM_PART_PREFIXES = sorted([
    'STRAINER', 'SUC MTH', 'DIFF', 'TAP CON PC', 'TAP CON',
    'NECK RING', 'IMP WEAR RING', 'IMP N/CAP', 'IMP DIST SLV', 'IMP',
    'BRG BUSH CARR', 'BRG BUSH', 'BRG HSG', 'I BRG BUSH',
    'INT BRG SLV', 'INT BRG CARR', 'SHAFT INT', 'SHAFT RH TOP', 'SHAFT RH',
    'P BRG SLV', 'DIST SLV', 'SAND COLL', 'GLD SLV', 'GLD SPLIT',
    'GLD PACK', 'LOCK NUT', 'SLV NUT', 'MUF COUP', 'SPT COLL',
    'ADJ RING', 'WATER DEFL', 'SOLE PLT', 'DBMS', 'ALIGN PAD',
    'L STF BOX', 'ST BOX LOOSE', 'STF BOX', 'LOG RING', 'ADPT PLT',
    'R.M.PIPE TAP', 'R.M.PIPE INT', 'R.M.PIPE TOP', 'R.M.PIPE BOT',
    'COOLING COIL', 'RATCHET',
], key=len, reverse=True)


# ── LLM Prompt ────────────────────────────────────────────────────────────

LLM_EVALUATION_PROMPT = """\
You are a senior pump engineering specialist and metallurgist reviewing \
material data for a vertical turbine pump.

═══════════════════════════════════════════════════════════
PUMP CONTEXT
═══════════════════════════════════════════════════════════
Pump model : {pump_name}
Stages     : {stages}

These parts have ALREADY passed a deterministic normalization check and \
still show a genuine family conflict. Your job is to determine if the \
conflict is a real engineering discrepancy or a known acceptable variant.

Documents:
  CS  — Cross-Section engineering drawing (material ground truth for wetted parts)
  BOM — Bill of Materials SAP export (material is abbreviated, secondary reference)
  SAP — SAP Configurator (authoritative for ~20 key wetted parts; absent for others)

═══════════════════════════════════════════════════════════
AUTHORITY RULE
═══════════════════════════════════════════════════════════
When sources disagree, apply this priority:
  1. If SAP and CS agree → they are correct. BOM is wrong. Flag BOM.
  2. If SAP and BOM agree but CS differs → SAP+BOM are correct. Flag CS.
  3. If only CS and BOM disagree (SAP absent) → flag for manual review.
  4. "Forged Steel" in SAP for Muff Coupling or shaft components is a known
     SAP configurator generic entry — it does NOT conflict with SS410 in CS/BOM.
     Clear this as a false positive.

═══════════════════════════════════════════════════════════
MATERIAL EQUIVALENCE (already applied — only genuine conflicts reach you)
═══════════════════════════════════════════════════════════
The following are already cleared before this prompt:
  SS410T = SS410, SS410H = SS410, CA15 = SS410
  CF8M = SS316, CF3M = SS316
  MS = M.S. = M.S. IS:2062 = M.S. IS:2062 GR-B = WCB
  FG260 = CI IS 210 GR FG260 = CI FG260
  CUTLESS RUBBER + SS410 = SS410 (composite: shell material is SS410)

Only flag if the BASE ALLOY FAMILIES are genuinely different:
  SS410 vs HTS  → FLAG
  FG260 vs MS   → FLAG
  CA6NM vs GGG50 → FLAG
  SS410 vs Forged Steel in SAP for couplings → CLEAR (known SAP generic entry)

═══════════════════════════════════════════════════════════
PARTS TO EVALUATE
═══════════════════════════════════════════════════════════

{parts_text}

═══════════════════════════════════════════════════════════
RESPONSE FORMAT
═══════════════════════════════════════════════════════════
Return ONLY a raw JSON array. No markdown, no code fences, no explanation outside JSON.

For every part:
{{
  "part": "<canonical part name — exactly as given>",
  "status": "CLEAR" or "FLAGGED",
  "authority": "CS" | "SAP" | "BOM" | "CS+SAP" | "SAP+BOM" | "MANUAL_REVIEW",
  "correct_material": "<the material that should be used, or null if unclear>",
  "discrepancies": [
    {{
      "type": "MATERIAL_MISMATCH | COATING_MISMATCH | MISSING",
      "source_in_error": "CS" | "BOM" | "SAP" | "UNKNOWN",
      "reason": "<one concise sentence: what is wrong and which document is incorrect>"
    }}
  ],
  "explanation": "<one sentence: why CLEAR or what the real problem is>"
}}

Rules:
  - "discrepancies" must be [] when status is CLEAR
  - "authority" must always be populated
  - Default bias is CLEAR — only flag genuine engineering problems
  - For FLAGGED parts, "source_in_error" must identify which document has the wrong value
"""


# ── Nomenclature ─────────────────────────────────────────────────────────

class Nomenclature:
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
        content = json.dumps(self.data, indent=2, ensure_ascii=False)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            f.write(content)
        tmp.replace(self.path)

    def _build_reverse_map(self) -> dict[str, str]:
        rev = {}
        for canonical, info in self.data.items():
            rev[canonical.upper()] = canonical
            for alias in info.get("aliases", []):
                rev[alias.upper()] = canonical
        return rev

    def resolve(self, name: str) -> str | None:
        if not name:
            return None
        return self._reverse.get(name.strip().upper())

    def add_alias(self, canonical: str, new_alias: str):
        if canonical not in self.data:
            self.data[canonical] = {"aliases": []}
        aliases = self.data[canonical]["aliases"]
        if new_alias not in aliases:
            aliases.append(new_alias)
            self._reverse[new_alias.upper()] = canonical
            self._save()
            logger.info(f"Added alias '{new_alias}' → '{canonical}'")

    def get_all_canonical(self) -> list[str]:
        return sorted(self.data.keys())


# ── Main Entry Point ──────────────────────────────────────────────────────

def compare(identifier: str, processed_dir: Path) -> dict:
    """Compare parts across CS, BOM, and SAP using two-pass logic."""
    nomenclature = Nomenclature()

    # Load part type sets from nomenclature (wetted_structural, structural, etc.)
    # This must happen before any comparison so MAJOR_WETTED_PARTS and
    # STRUCTURAL_PART_NAMES are populated for this pump's nomenclature file.
    load_part_type_sets(nomenclature.path)

    cs_data  = _load_json(processed_dir / "cs_bom.json",   default=[])
    bom_data = _load_json(processed_dir / "bom_data.json", default=[])
    sap_data = _load_json(processed_dir / "sap_data.json", default={})

    cs_parts,  cs_unresolved  = _normalize_cs(cs_data, nomenclature)
    bom_parts, bom_unresolved = _normalize_bom(bom_data, nomenclature)
    sap_parts, sap_unresolved, sap_metadata = _normalize_sap(sap_data, nomenclature)

    # Resolve coating requirement from SAP metadata once, pass to all comparisons
    coating_required = _resolve_coating_requirement(sap_metadata)
    stages = _parse_stages(sap_metadata.get("No of Stages", "1"))

    all_canonical = sorted(
        set(cs_parts.keys()) | set(bom_parts.keys()) | set(sap_parts.keys())
    )

    # ── Phase 1: Build context per part ───────────────────────────────────
    all_parts = []
    for canonical in all_canonical:
        ctx = _build_part_context(
            canonical, cs_parts, bom_parts, sap_parts,
            coating_required=coating_required,
            stages=stages,
        )
        all_parts.append(ctx)

    # ── Phase 2: Rigid pass ───────────────────────────────────────────────
    clear_parts = []
    needs_llm   = []
    for ctx in all_parts:
        result = _rigid_evaluate(ctx)
        if result["clear"]:
            ctx["discrepancies"] = []
            ctx["rigid_result"]  = result
            clear_parts.append(ctx)
        else:
            ctx["rigid_result"] = result
            needs_llm.append(ctx)

    logger.info(
        f"Rigid pass: {len(clear_parts)} clear, {len(needs_llm)} need LLM review"
    )

    # ── Phase 3: LLM evaluation (only genuine family conflicts) ────────────
    if needs_llm:
        _llm_evaluate_all(needs_llm, sap_metadata)

    # ── Assemble results ──────────────────────────────────────────────────
    parts_comparison = sorted(
        [_clean_for_output(p) for p in clear_parts + needs_llm],
        key=lambda p: p["canonical_name"],
    )

    # Deduplicate unresolved list by (source, original_name) to prevent the
    # same part name appearing multiple times when a CS drawing lists the same
    # part description across multiple rows (different refs or quantities).
    # Keep first occurrence of each unique (source, original_name) pair.
    seen_unresolved = set()
    deduped_unresolved = []
    for u in cs_unresolved + bom_unresolved + sap_unresolved:
        key = (u.get("source", ""), u.get("original_name", "").upper().strip())
        if key not in seen_unresolved:
            seen_unresolved.add(key)
            deduped_unresolved.append(u)
    unresolved = deduped_unresolved

    total_discrepancies = sum(len(p["discrepancies"]) for p in parts_comparison)

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
        "sap_metadata": sap_metadata,
    }

    output_path = processed_dir / "comparison_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    logger.info(
        f"Comparison complete for {identifier}: "
        f"{len(all_canonical)} parts, {total_discrepancies} discrepancies"
    )
    return results


# ── Normalization ─────────────────────────────────────────────────────────

def _clean_cs_description(desc: str) -> list[str]:
    """
    Generate lookup candidates from a raw CS drawing description string.

    CS drawings from different pump families use varying conventions:
      - Trailing dots:           "PUMP SHAFT."  -> "PUMP SHAFT"
      - Parenthetical locations: "BEARING BUSH. (PUMP)" -> "BEARING BUSH"
      - Abbreviations with dots: "BRG. HOUSING." -> "BRG. HOUSING" / "BRG HOUSING"

    Returns a list of candidates to try in order (most specific first).
    The nomenclature resolver tries each until one matches.
    """
    import re as _re
    candidates = []

    # Candidate 1: original (already stripped of outer whitespace)
    candidates.append(desc)

    # Candidate 2: strip trailing dot(s) and spaces
    stripped = desc.rstrip(". ").strip()
    if stripped != desc:
        candidates.append(stripped)

    # Candidate 3: strip parenthetical suffix e.g. "(PUMP)", "(MOTOR HALF)", "(IMP)"
    # This handles: "BEARING BUSH. (PUMP)." -> "BEARING BUSH"
    no_paren = _re.sub(r"[\s]*[(][^)]*[)]\.?$", "", stripped).strip().rstrip(". ").strip()
    if no_paren and no_paren not in candidates:
        candidates.append(no_paren)

    # Candidate 4: normalize internal dots in abbreviations
    # "BRG. HOUSING." -> "BRG HOUSING"
    no_abbrev_dots = _re.sub(r"[.][ ]+", " ", no_paren).strip()
    if no_abbrev_dots and no_abbrev_dots not in candidates:
        candidates.append(no_abbrev_dots)

    return candidates


def _normalize_cs(cs_data: list, nom: Nomenclature) -> tuple[dict, list]:
    parts      = {}
    unresolved = []

    for entry in cs_data:
        desc = (entry.get("description") or "").strip()
        if not desc or desc.upper() in ("DESCRIPTION", "REF.", "MATERIAL."):
            continue
        if _is_fastener_or_generic(desc):
            continue

        # Try multiple cleaned candidates before giving up
        canonical = None
        for candidate in _clean_cs_description(desc):
            canonical = nom.resolve(candidate) or _try_partial_resolve(candidate, nom)
            if canonical:
                break

        if canonical:
            if canonical not in parts:
                raw_mat = entry.get("material")

                # CS sanity check: flag consumable material or coating brand
                # on a structural part — both indicate a CS extraction error.
                cs_extraction_warning = None
                if canonical in STRUCTURAL_PART_NAMES and raw_mat:
                    if is_consumable_material(raw_mat):
                        cs_extraction_warning = (
                            f"CS extraction suspect: '{raw_mat}' looks like a consumable "
                            f"material assigned to structural part '{canonical}'. "
                            f"Likely a PDF row-span extraction error. "
                            f"CS material excluded from comparison."
                        )
                    elif is_coating_brand(raw_mat):
                        cs_extraction_warning = (
                            f"CS extraction suspect: '{raw_mat}' looks like a coating "
                            f"product name, not a base material grade, for structural "
                            f"part '{canonical}'. Likely the extractor captured a coating "
                            f"annotation instead of the alloy code. "
                            f"CS material excluded from comparison."
                        )
                    if cs_extraction_warning:
                        logger.warning(cs_extraction_warning)
                        raw_mat = None  # Exclude from comparison

                parts[canonical] = {
                    "present":  True,
                    "material": raw_mat,
                    "qty":      entry.get("qty"),
                    "cs_extraction_warning": cs_extraction_warning,
                }
        else:
            unresolved.append({
                "source":        "cs",
                "original_name": desc,
                "ref":           entry.get("ref"),
            })

    return parts, unresolved


def _normalize_bom(bom_data: list, nom: Nomenclature) -> tuple[dict, list]:
    parts      = {}
    unresolved = []

    for entry in bom_data:
        desc = (entry.get("description") or "").strip()
        if not desc:
            continue

        prefix = _extract_bom_prefix(desc)
        if not prefix:
            continue

        canonical = nom.resolve(prefix) or _try_partial_resolve(prefix, nom)
        if not canonical:
            canonical = nom.resolve(desc) or _try_partial_resolve(desc, nom)

        if not canonical:
            unresolved.append({
                "source":        "bom",
                "original_name": desc,
                "item_number":   entry.get("item_number"),
            })
            continue

        material, has_coating = _extract_material_from_bom_desc(desc)

        if canonical not in parts:
            parts[canonical] = {
                "present":  True,
                "material": material,
                "qty":      entry.get("quantity"),
                "coating":  has_coating,
            }

    return parts, unresolved


def _normalize_sap(
    sap_data: dict, nom: Nomenclature
) -> tuple[dict, list, dict]:
    parts      = {}
    unresolved = []
    metadata   = {}

    entries = sap_data.get("entries", [])

    for entry in entries:
        key   = (entry.get("key")   or "").strip()
        value = (entry.get("value") or "")
        if not key or value is None:
            continue

        value_str = str(value).strip()
        if not value_str:
            continue

        canonical = nom.resolve(key)

        if canonical:
            if canonical not in parts:
                has_coating = "COATING" in value_str.upper()
                parts[canonical] = {
                    "present":      True,
                    "material":     value_str,
                    "raw_material": value_str,
                    "coating":      has_coating,
                }
        else:
            metadata[key] = value_str

    return parts, unresolved, metadata


# ── Part Context Builder ──────────────────────────────────────────────────

def _build_part_context(
    canonical: str,
    cs_parts: dict,
    bom_parts: dict,
    sap_parts: dict,
    coating_required: bool = False,
    stages: int = 1,
) -> dict:
    cs  = cs_parts.get(canonical)
    bom = bom_parts.get(canonical)
    sap = sap_parts.get(canonical)

    raw_materials = {}
    coating_flags = {}
    cs_warning    = None

    if cs:
        if cs.get("material"):
            raw_materials["cs"] = cs["material"]
        coating_flags["cs"] = "COATING" in (cs.get("material") or "").upper()
        cs_warning = cs.get("cs_extraction_warning")

    if bom:
        if bom.get("material"):
            raw_materials["bom"] = bom["material"]
        coating_flags["bom"] = bool(bom.get("coating", False))

    if sap:
        if sap.get("material"):
            raw_materials["sap"] = sap["material"]
        coating_flags["sap"] = bool(sap.get("coating", False))

    # Material comparison with coating_required context
    if len(raw_materials) >= 2:
        mat_comparison = rigid_materials_match(
            raw_materials, coating_flags, coating_required=coating_required
        )
        mat_comparison["method"] = "rigid"
    elif len(raw_materials) == 1:
        mat_comparison = {
            "method": "rigid", "result": "INSUFFICIENT",
            "normalized": {}, "families": {}, "coating_match": None,
            "explanation": "Only one source has material data",
        }
    else:
        mat_comparison = {
            "method": "rigid", "result": "INSUFFICIENT",
            "normalized": {}, "families": {}, "coating_match": None,
            "explanation": "No material data available",
        }

    return {
        "canonical_name":    canonical,
        "cs":                cs,
        "bom":               bom,
        "sap":               sap,
        "material_comparison": mat_comparison,
        "coating_required":  coating_required,
        "cs_extraction_warning": cs_warning,
        "discrepancies":     [],
    }


# ── Rigid Evaluation ──────────────────────────────────────────────────────

def _rigid_evaluate(ctx: dict) -> dict:
    """
    Evaluate a part using the authority model and return a result dict.

    Returns:
        {
            "clear": bool,
            "reason": str,   # why it was cleared or why it needs LLM
            "warnings": list # extraction warnings to include in report
        }
    """
    canonical = ctx["canonical_name"]
    cs  = ctx.get("cs")
    bom = ctx.get("bom")
    sap = ctx.get("sap")
    mat = ctx["material_comparison"]
    warnings = []

    # Collect any CS extraction warning
    if ctx.get("cs_extraction_warning"):
        warnings.append({
            "type":   "CS_EXTRACTION_WARNING",
            "reason": ctx["cs_extraction_warning"],
        })

    # ── Absence checks ────────────────────────────────────────────────────
    # SAP absence is ALWAYS expected — never flag
    # BOM absence: only flag for major wetted parts
    if not bom and canonical in MAJOR_WETTED_PARTS:
        return {
            "clear": False,
            "reason": f"Major wetted part '{canonical}' is absent from BOM",
            "warnings": warnings,
        }

    # CS absence: flag if part has no presence in CS and is a major structural part
    if not cs and canonical in MAJOR_WETTED_PARTS:
        return {
            "clear": False,
            "reason": f"Major wetted part '{canonical}' is absent from CS drawing",
            "warnings": warnings,
        }

    # ── Material comparison ───────────────────────────────────────────────
    result = mat["result"]

    # INSUFFICIENT = only one source has material data
    # Under the authority model, this is fine — nothing to compare
    if result == "INSUFFICIENT":
        return {
            "clear": True,
            "reason": "Only one source has material data — no comparison possible",
            "warnings": warnings,
        }

    # MATCH = all sources with material data resolve to same family
    if result == "MATCH":
        # Check coating separately
        if mat.get("coating_match") is False and not ctx["coating_required"]:
            return {
                "clear": False,
                "reason": "Material families match but coating flags differ",
                "warnings": warnings,
            }
        return {
            "clear": True,
            "reason": mat.get("explanation", "All sources agree"),
            "warnings": warnings,
        }

    # MISMATCH = genuine family conflict — apply two checks before sending to LLM

    # Check A: if CS material was excluded (consumable extraction warning),
    # re-evaluate with BOM and SAP only
    if result == "MISMATCH" and ctx.get("cs_extraction_warning"):
        bom_mat = (bom or {}).get("material")
        sap_mat = (sap or {}).get("material")
        if bom_mat and sap_mat:
            reduced = rigid_materials_match(
                {"bom": bom_mat, "sap": sap_mat},
                {"bom": bool((bom or {}).get("coating", False)),
                 "sap": bool((sap or {}).get("coating", False))},
                coating_required=ctx["coating_required"],
            )
            if reduced["result"] == "MATCH":
                return {
                    "clear": True,
                    "reason": "BOM and SAP agree; CS excluded due to extraction warning",
                    "warnings": warnings,
                }

    # Check B: Cross-source confidence check (Fix 2 — Strainer false positive)
    # If BOM and SAP both have material data and AGREE with each other,
    # but CS shows a completely different family, this is almost certainly
    # a CS extraction error (row-span from an adjacent part), not a real mismatch.
    # Add a CS_EXTRACTION_WARNING and clear the flag — BOM+SAP agreement
    # is authoritative when they both point to the same family.
    #
    # Safety guard: only apply this when:
    #   1. BOM and SAP are both present and agree
    #   2. CS family is genuinely different (not just a notation variant)
    #   3. No CS extraction warning already exists (avoid double-warning)
    if result == "MISMATCH" and not ctx.get("cs_extraction_warning"):
        bom_mat = (bom or {}).get("material")
        sap_mat = (sap or {}).get("material")
        cs_mat  = (cs or {}).get("material")

        if bom_mat and sap_mat and cs_mat:
            bom_sap_check = rigid_materials_match(
                {"bom": bom_mat, "sap": sap_mat},
                {"bom": bool((bom or {}).get("coating", False)),
                 "sap": bool((sap or {}).get("coating", False))},
                coating_required=ctx["coating_required"],
            )
            if bom_sap_check["result"] == "MATCH":
                # BOM and SAP agree — CS is the outlier
                # Flag it as a likely extraction error
                cross_source_warning = (
                    f"CS extraction suspect: CS shows '{cs_mat}' but BOM and SAP "
                    f"both agree on a different material family. "
                    f"Likely a PDF row-span error where the material from an adjacent "
                    f"part was assigned to this row. CS material excluded from comparison."
                )
                logger.warning(cross_source_warning)
                warnings.append({
                    "type":   "CS_EXTRACTION_WARNING",
                    "reason": cross_source_warning,
                })
                # Update the context so downstream code knows CS was excluded
                if ctx.get("cs"):
                    ctx["cs"]["cs_extraction_warning"] = cross_source_warning
                    ctx["cs"]["material"] = None
                ctx["cs_extraction_warning"] = cross_source_warning
                return {
                    "clear": True,
                    "reason": "BOM and SAP agree; CS outlier excluded as likely extraction error",
                    "warnings": warnings,
                }

    return {
        "clear": False,
        "reason": mat.get("explanation", "Family conflict between sources"),
        "warnings": warnings,
    }


def _clean_for_output(part: dict) -> dict:
    """Prepare a part context dict for JSON output."""
    rigid_result = part.get("rigid_result", {})
    warnings = rigid_result.get("warnings", [])

    # Merge warnings into discrepancies list as WARNING type
    discrepancies = list(part.get("discrepancies", []))
    for w in warnings:
        # Only add if not already present
        if not any(d.get("type") == w["type"] for d in discrepancies):
            discrepancies.append(w)

    return {
        "canonical_name":    part["canonical_name"],
        "cs":                part["cs"],
        "bom":               part["bom"],
        "sap":               part["sap"],
        "material_comparison": part["material_comparison"],
        "discrepancies":     discrepancies,
    }


# ── Pass 2: LLM Evaluation ────────────────────────────────────────────────

def _llm_evaluate_all(parts: list[dict], sap_metadata: dict):
    if not config.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set — falling back to conservative flagging")
        _fallback_flag_all(parts)
        return

    stages    = sap_metadata.get("No of Stages", "unknown")
    pump_name = sap_metadata.get("VT pump Common Name", "Unknown Pump")

    parts_text = "\n\n".join(
        _format_part_for_prompt(i, p) for i, p in enumerate(parts, 1)
    )

    prompt = LLM_EVALUATION_PROMPT.format(
        pump_name=pump_name,
        stages=stages,
        parts_text=parts_text,
    )

    llm_results = _call_gemini_with_retry(prompt, retries=2)

    if llm_results is None:
        logger.error("LLM evaluation failed — falling back to conservative flagging")
        _fallback_flag_all(parts)
        return

    _apply_llm_results(parts, llm_results)


def _format_part_for_prompt(index: int, part: dict) -> str:
    """Format one part for the LLM prompt — concise and information-dense."""
    name  = part["canonical_name"]
    lines = [f"{index}. {name}"]

    for src in ("cs", "bom", "sap"):
        entry = part.get(src)
        if not entry:
            lines.append(f"   {src.upper()}: absent")
            continue
        mat     = entry.get("material")
        coating = entry.get("coating", False)
        mat_str = f'"{mat}"' if mat else "not specified"
        coat_str = " | coating: YES" if coating else ""
        lines.append(f"   {src.upper()}: material={mat_str}{coat_str}")

    # Show the family conflict explicitly
    mat_comp = part["material_comparison"]
    families = mat_comp.get("families", {})
    if families:
        fam_str = " vs ".join(f"{s}={f}" for s, f in families.items())
        lines.append(f"   Family conflict: {fam_str}")

    return "\n".join(lines)


def _apply_llm_results(parts: list[dict], llm_results: list[dict]):
    llm_by_name = {r.get("part", ""): r for r in llm_results}

    for part in parts:
        llm_result = llm_by_name.get(part["canonical_name"])
        if not llm_result:
            logger.warning(f"LLM returned no result for '{part['canonical_name']}' — fallback")
            _fallback_flag_single(part)
            continue

        status        = llm_result.get("status", "CLEAR")
        explanation   = llm_result.get("explanation", "")
        authority     = llm_result.get("authority", "MANUAL_REVIEW")
        correct_mat   = llm_result.get("correct_material")
        discrepancies = llm_result.get("discrepancies", [])

        part["material_comparison"]["method"]          = "llm"
        part["material_comparison"]["explanation"]     = explanation
        part["material_comparison"]["authority"]       = authority
        part["material_comparison"]["correct_material"] = correct_mat

        if status == "FLAGGED" and discrepancies:
            if any(d.get("type") == "MATERIAL_MISMATCH" for d in discrepancies):
                part["material_comparison"]["result"] = "MISMATCH"
            for disc in discrepancies:
                part["discrepancies"].append({
                    "type":            disc.get("type", "UNKNOWN"),
                    "source_in_error": disc.get("source_in_error", "UNKNOWN"),
                    "reason":          disc.get("reason", "Flagged by AI evaluation"),
                    "detail":          disc.get("reason", ""),
                    "authority":       authority,
                    "correct_material": correct_mat,
                })
        else:
            # LLM cleared it — override any MISMATCH from rigid pass
            if part["material_comparison"]["result"] == "MISMATCH":
                part["material_comparison"]["result"] = "MATCH"
            # Keep extraction warnings but clear material discrepancies
            part["discrepancies"] = [
                d for d in part["discrepancies"]
                if d.get("type") == "CS_EXTRACTION_WARNING"
            ]


def _fallback_flag_all(parts: list[dict]):
    for part in parts:
        _fallback_flag_single(part)


def _fallback_flag_single(part: dict):
    part["material_comparison"]["method"] = "fallback"
    part["material_comparison"]["explanation"] = (
        "AI evaluation unavailable — flagged for manual review"
    )
    mat = part["material_comparison"]
    if mat["result"] == "MISMATCH":
        mats = {}
        for s in ("cs", "bom", "sap"):
            entry = part.get(s)
            if entry and entry.get("material"):
                mats[s] = entry["material"]
        part["discrepancies"].append({
            "type":            "MATERIAL_MISMATCH",
            "source_in_error": "UNKNOWN",
            "reason":          f"Material conflict requires manual review: "
                               + ", ".join(f"{s}: {m}" for s, m in mats.items()),
            "detail":          str(mats),
            "authority":       "MANUAL_REVIEW",
            "correct_material": None,
        })


def _call_gemini_with_retry(prompt: str, retries: int = 2) -> list | None:
    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(config.GEMINI_MODEL)

    for attempt in range(retries + 1):
        try:
            response = model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                ),
            )
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            result = json.loads(raw)
            if isinstance(result, list):
                logger.info(f"LLM evaluation returned {len(result)} results")
                return result
            logger.warning(f"LLM returned non-list JSON: {type(result)}")
            return None

        except json.JSONDecodeError as e:
            logger.error(f"LLM JSON parse error (attempt {attempt + 1}): {e}")
        except Exception as e:
            logger.error(f"LLM API error (attempt {attempt + 1}): {e}")

        if attempt < retries:
            wait = 2 ** attempt
            logger.info(f"Retrying LLM call in {wait}s...")
            time.sleep(wait)

    return None


# ── BOM description helpers ───────────────────────────────────────────────

def _extract_bom_prefix(description: str) -> str | None:
    upper = description.upper()
    for prefix in _BOM_PART_PREFIXES:
        if upper.startswith(prefix):
            return prefix
    return None


def _extract_material_from_bom_desc(desc: str) -> tuple[str | None, bool]:
    upper       = desc.upper().strip()
    has_coating = bool(re.search(r"\+\s*COAT", upper.replace(" ", "")))

    patterns = [
        r"(CA\d+\w*)(?:\+COAT)?$",
        r"(GGG\d+)(?:\+COAT)?$",
        r"(SS\s?\d{3}\w*)(?:\+CUTRUB)?$",
        r"CUT\s*RUB\w*\s+(SS\d+)$",
        r"(SS\d+)\+CUTRUB$",
        r"(FG\s?\d+)(?:\+COAT)?$",
        r"\b(HTS)$",
        r"\b(MS)$",
    ]

    for pat in patterns:
        m = re.search(pat, upper)
        if m:
            result = m.group(1).strip()
            if has_coating and "COAT" not in result:
                result += " + COATING"
            return result, has_coating

    if "GRAPHITED" in upper and "COTTON" in upper:
        return "GRAPHITED COTTON", False
    if "NITRILE" in upper:
        return "NITRILE RUBBER", False

    return None, has_coating


# ── Shared helpers ────────────────────────────────────────────────────────

def _is_fastener_or_generic(desc: str) -> bool:
    upper = desc.upper()
    keywords = [
        "FASTNER", "FASTENER", "GASKET", "O' RING", "O RING", "'O' RING",
        "WASHER", "STUD", "HEX NUT", "HEX HD SCR", "SOC SET SCR",
        "SOC HD CAP", "HEX PLUG", "DOWEL PIN", "RIVET", "ERECTION PACKER",
        "FOUNDATION BOLT", "NAME PLATE", "INDICATOR ARROW", "CORD ",
        "BES KEY", "S-BER KEY", "KEY ", " KEY",
        "GLAND PACKING", "GLD PACK",
    ]
    return any(kw in upper for kw in keywords)


def _try_partial_resolve(desc: str, nom: Nomenclature) -> str | None:
    words = desc.split()
    for n in range(min(4, len(words)), 1, -1):
        result = nom.resolve(" ".join(words[:n]))
        if result:
            return result
    return None


def _resolve_coating_requirement(sap_metadata: dict) -> bool:
    """Return True if SAP metadata says coating is required for this pump."""
    val = sap_metadata.get("Coating Reqd By Customer", "").upper()
    return val in ("YES", "Y", "TRUE", "1")


def _parse_stages(stages_str: str) -> int:
    """Parse the number of stages from SAP metadata string."""
    try:
        return int(str(stages_str).strip().lstrip("0") or "1")
    except (ValueError, TypeError):
        return 1


def _load_json(path: Path, default=None):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


# ── Validation ────────────────────────────────────────────────────────────

def apply_validation(
    identifier: str, processed_dir: Path, decisions: list[dict]
) -> dict:
    results      = _load_json(processed_dir / "comparison_results.json", default={})
    nomenclature = Nomenclature()
    confirmed    = []
    dismissed    = []

    for decision in decisions:
        canonical  = decision["canonical_name"]
        action     = decision["action"]
        disc_index = decision.get("discrepancy_index", 0)

        if action == "agree":
            reason = None
            for part in results.get("parts", []):
                if part["canonical_name"] == canonical:
                    discs = part.get("discrepancies", [])
                    if disc_index < len(discs):
                        reason = discs[disc_index].get("reason")
                    break
            confirmed.append({
                "canonical_name":    canonical,
                "discrepancy_index": disc_index,
                "reason":            reason,
            })
        elif action == "disagree":
            mapped        = decision.get("mapped_canonical")
            original_name = decision.get("original_name")
            if mapped and original_name:
                nomenclature.add_alias(mapped, original_name)
            dismissed.append({
                "canonical_name":    canonical,
                "discrepancy_index": disc_index,
                "mapped_to":         mapped,
            })

    validation_status = {
        "identifier":              identifier,
        "timestamp":               datetime.now(timezone.utc).isoformat(),
        "confirmed_discrepancies": confirmed,
        "dismissed_discrepancies": dismissed,
        "total_confirmed":         len(confirmed),
        "total_dismissed":         len(dismissed),
    }

    with open(processed_dir / "validation_status.json", "w") as f:
        json.dump(validation_status, f, indent=2)

    logger.info(
        f"Validation for {identifier}: "
        f"{len(confirmed)} confirmed, {len(dismissed)} dismissed"
    )
    return validation_status