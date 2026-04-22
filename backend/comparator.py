"""
Part comparison engine for the BOM Components Validator.

Two-pass architecture:
  Pass 1 — Rigid string comparison: strips spec prefixes, normalizes dots,
           separates coating, handles "/" separators. Conservative — only
           clears parts where ALL signals (presence, material, quantity,
           coating) match across every source. Zero false-positive risk.
  Pass 2 — Gemini LLM evaluation: ALL remaining parts (not just material
           mismatches) are sent in a single batched call. The LLM applies
           domain knowledge to decide which potential issues are genuine
           engineering discrepancies vs. expected behavior.

Data shapes consumed (new extractor outputs):
  cs_bom.json    → list of {ref, description, qty, material}
  bom_data.json  → list of {item_number, component_number, description,
                             quantity, unit, text1, text2, sort_string}
  sap_data.json  → {entries: [{key, value}], design_text: str}
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
    normalize_for_rigid_comparison,
    rigid_materials_match,
)

logger = logging.getLogger(__name__)

NOMENCLATURE_PATH = config.BACKEND_DIR / "nomenclature.json"

# ── BOM description: part prefix abbreviations (longest first) ─────────────
# Used by _normalize_bom to identify the part type from the raw description.
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

# ── Gemini LLM Prompt ──────────────────────────────────────────────────────

LLM_EVALUATION_PROMPT = """\
You are a senior pump engineering specialist and metallurgist reviewing \
material and quantity data for a vertical turbine pump.

═══════════════════════════════════════════════════════════
PUMP CONTEXT
═══════════════════════════════════════════════════════════
Pump model : {pump_name}
Stages     : {stages}
Each part below has been extracted from three source documents:

  CS  — Cross-Section engineering drawing
        • Contains every wetted and structural part
        • Quantities are PER STAGE (not total)
        • Material column uses merged cells — some rows have no material
          printed in the drawing itself (shown as null/not specified here).
          A null CS material is NOT a discrepancy; it simply means the
          drawing did not annotate that row.

  BOM — Bill of Materials (SAP Excel export)
        • Contains every procurable item
        • Quantities are TOTAL across all stages
        • For parts with multiple sub-variants (e.g. Bearing Bush appears
          in different locations), the BOM quantity shown here is for ONE
          sub-variant only — the total across all variants will be higher.
        • Material is extracted from the SAP description code string
          (e.g. "IMP 2 5638 1500 0501 CA6NM+COAT" → CA6NM + COATING)

  SAP — SAP Configurator data
        • Stores MOC (Material of Construction) for ~20 KEY wetted parts only
        • Hardware, accessories, fasteners, and minor parts are intentionally
          absent from SAP — this is EXPECTED, never a discrepancy
        • SAP NEVER has quantity data — "not specified" for SAP qty is always
          expected and must never be flagged as a quantity discrepancy
        • SAP material values use full spec notation:
          e.g. "ASTM A276 GR SS410" or "A276 GR SS410(T Condition)"

═══════════════════════════════════════════════════════════
MATERIAL EQUIVALENCE RULES  ← apply these before flagging
═══════════════════════════════════════════════════════════
These are the same material — do NOT flag as mismatch:

NOTATION VARIANTS (same alloy, different spec prefix):
  • "ASTM A276 GR SS410" = "A276 GR SS410" = "SS410" = "SS 410"
  • "ASTM A743 GR CA15/SS410" = "CA15" = "SS410" (CA15 IS SS410)
  • "ASTM A743 GR CF8M" = "CF8M" = "SS316L equivalent"
  • "CI IS 210 GR FG260" = "FG260" = "Cast Iron FG260"
  • "M.S. IS:2062 GR-B" = "MS" = "M.S." = "Mild Steel"
  • "GGG50" = "SG Iron GGG50" = "Ductile Iron GGG50"
  • "CA6NM" = "ASTM A743 GR CA6NM" (martensitic stainless)

HEAT TREATMENT SUFFIXES — same base alloy:
  • "SS410T" = "SS410(T Condition)" = "SS410" (T = tempered)
  • "SS410H" = "SS410" (H = hardened, acceptable variant)

COMPOSITE MATERIALS — structural shell determines the alloy:
  • "CUTLESS RUBBER + SS410 SHELL" = "SS410" = "CUTLESS + SS410"
    (Cutless rubber is the bearing liner; SS410 is the structural shell)
  • All three notations above are the same part — CLEAR

MANUFACTURER STANDARDS — not a comparable material value:
  • "MFG.STD" / "M&P Std" / "Standard (MS)" / "NO. 29440E" = manufacturer
    standard. Do not compare against a specific alloy — treat as UNKNOWN.

GENUINELY DIFFERENT MATERIALS (flag these):
  • SS410 vs HTS (high tensile steel — different family entirely)
  • SS410 vs FG260 (stainless vs cast iron — completely different)
  • CA6NM vs GGG50 (stainless steel casting vs ductile iron)
  • MS vs SS304 (mild steel vs stainless — real difference for wetted parts)

═══════════════════════════════════════════════════════════
QUANTITY RULES
═══════════════════════════════════════════════════════════
This pump has {stages} stage(s). Apply these rules before flagging qty:

1. STAGE MULTIPLICATION: BOM qty = CS qty × {stages} for staged parts
   (impellers, diffusers, neck rings, wear rings, shaft segments, etc.)
   Example: CS qty=1 + BOM qty=2 on a 2-stage pump → CLEAR

2. SAP HAS NO QTY: "not specified" in SAP is always expected → never flag

3. BOM SUB-VARIANT ISSUE: For parts that appear in multiple locations
   (Bearing Bush in diffuser, bell mouth, stuffing box, etc.), the BOM qty
   shown here is for ONE location only. Do not flag qty mismatches for
   Bearing Bush, Pump Bearing Sleeve, Intermediate Shaft variants, or
   RM Pipe variants — these have multiple BOM line items.

4. FLAG ONLY: When CS and BOM quantities differ AND the difference cannot
   be explained by stage multiplication or the sub-variant issue above.

═══════════════════════════════════════════════════════════
PRESENCE RULES
═══════════════════════════════════════════════════════════
Only flag MISSING when the absence is genuinely unexpected:

  SAP absent  → NEVER flag. SAP only covers ~20 key wetted parts.
                Hardware, accessories, and minor parts are intentionally absent.

  BOM absent  → Flag only for major wetted parts (impeller, shaft, diffuser,
                neck ring, etc.). Hardware absence from BOM is acceptable.

  CS absent   → Flag only if a critical structural part is completely missing
                from the drawing — very rare.

═══════════════════════════════════════════════════════════
HOW TO USE THE RIGID CHECK
═══════════════════════════════════════════════════════════
Each part includes a "Rigid check" line showing what our string-normalizer found:

  MATCH       → Materials agree after normalization. Unless there is a
                genuine qty discrepancy (not explainable by the rules above),
                output CLEAR immediately — no further analysis needed.

  MISMATCH    → String normalizer could not reconcile the values.
                Apply your domain knowledge using the equivalence rules above.
                Most "mismatches" will actually be the same material in
                different notation — output CLEAR for those.
                Only output FLAGGED if the alloy families are genuinely different.

  INSUFFICIENT → One or more sources lack material data. Look at what IS
                available. If CS material is null but BOM and SAP agree →
                materials are consistent, output CLEAR. If only one source
                has material, output CLEAR (nothing to compare against).

═══════════════════════════════════════════════════════════
PARTS TO EVALUATE
═══════════════════════════════════════════════════════════

{parts_text}

═══════════════════════════════════════════════════════════
RESPONSE FORMAT
═══════════════════════════════════════════════════════════
Return ONLY a raw JSON array. No markdown, no code fences, no explanation.

For every part, output exactly one object:
{{
  "part": "<canonical part name — exactly as given>",
  "status": "CLEAR" or "FLAGGED",
  "discrepancies": [
    {{
      "type": "MATERIAL_MISMATCH | MISSING | QUANTITY_MISMATCH | COATING_MISMATCH",
      "reason": "<one concise sentence explaining the specific issue>"
    }}
  ],
  "explanation": "<one sentence: why CLEAR or what the real problem is>"
}}

Rules for the response:
- "discrepancies" must be an empty array [] when status is CLEAR
- Only include a discrepancy type that is genuinely present
- "explanation" must always be populated — never leave it empty
- The "part" value must exactly match the canonical name as given in the input
- Default bias is CLEAR — only flag genuine engineering problems
"""


# ── Nomenclature ───────────────────────────────────────────────────────────

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


# ── Main Comparison Entry Point ─────────────────────────────────────────────

def compare(identifier: str, processed_dir: Path) -> dict:
    """
    Compare parts across CS, BOM, and SAP extracted data using two-pass logic.
    """
    nomenclature = Nomenclature()

    cs_data  = _load_json(processed_dir / "cs_bom.json",   default=[])
    bom_data = _load_json(processed_dir / "bom_data.json", default=[])
    sap_data = _load_json(processed_dir / "sap_data.json", default={})

    # Normalize each source into {canonical_name: {material, qty, ...}}
    cs_parts,  cs_unresolved  = _normalize_cs(cs_data, nomenclature)
    bom_parts, bom_unresolved = _normalize_bom(bom_data, nomenclature)
    sap_parts, sap_unresolved, sap_metadata = _normalize_sap(sap_data, nomenclature)

    all_canonical = sorted(
        set(cs_parts.keys()) | set(bom_parts.keys()) | set(sap_parts.keys())
    )

    # ── Phase 1: Build context per part ───────────────────────────────────
    all_parts = []
    for canonical in all_canonical:
        ctx = _build_part_context(canonical, cs_parts, bom_parts, sap_parts)
        all_parts.append(ctx)

    # ── Phase 2: Rigid pass ────────────────────────────────────────────────
    clear_parts = []
    needs_llm   = []
    for ctx in all_parts:
        if _is_fully_clear(ctx):
            ctx["discrepancies"] = []
            clear_parts.append(ctx)
        else:
            needs_llm.append(ctx)

    logger.info(
        f"Rigid pass: {len(clear_parts)} clear, {len(needs_llm)} need LLM review"
    )

    # ── Phase 3: LLM evaluation ────────────────────────────────────────────
    if needs_llm:
        _llm_evaluate_all(needs_llm, sap_metadata)

    # ── Assemble results ───────────────────────────────────────────────────
    parts_comparison = sorted(
        [_clean_for_output(p) for p in clear_parts + needs_llm],
        key=lambda p: p["canonical_name"],
    )

    unresolved = cs_unresolved + bom_unresolved + sap_unresolved
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
        # Pass metadata through so report.py can read it
        "sap_metadata": sap_metadata,
    }

    output_path = processed_dir / "comparison_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    logger.info(
        f"Comparison complete for {identifier}: "
        f"{len(all_canonical)} parts, {total_discrepancies} discrepancies, "
        f"{len(unresolved)} unresolved"
    )
    return results


# ── Normalization ──────────────────────────────────────────────────────────

def _normalize_cs(cs_data: list, nom: Nomenclature) -> tuple[dict, list]:
    """Normalize CS BOM entries → {canonical: {material, qty}}."""
    parts      = {}
    unresolved = []

    for entry in cs_data:
        desc = (entry.get("description") or "").strip()
        if not desc or desc.upper() in ("DESCRIPTION", "REF.", "MATERIAL."):
            continue
        if _is_fastener_or_generic(desc):
            continue

        canonical = nom.resolve(desc) or _try_partial_resolve(desc, nom)

        if canonical:
            if canonical not in parts:
                parts[canonical] = {
                    "present":  True,
                    "material": entry.get("material"),
                    "qty":      entry.get("qty"),
                }
        else:
            unresolved.append({
                "source":        "cs",
                "original_name": desc,
                "ref":           entry.get("ref"),
            })

    return parts, unresolved


def _normalize_bom(bom_data: list, nom: Nomenclature) -> tuple[dict, list]:
    """
    Normalize BOM Excel entries → {canonical: {material, qty, coating}}.

    BOM descriptions are raw SAP strings like:
        'IMP 2 5638 1500 0501 CA6NM+COAT'
    We extract:
      - Part identity: by matching the known prefix abbreviation list against
        the start of the description, then resolving via nomenclature.
      - Material: by regex-matching the last token(s) of the description.
      - Coating: by detecting '+COAT' in the description string.
    """
    parts      = {}
    unresolved = []

    for entry in bom_data:
        desc = (entry.get("description") or "").strip()
        if not desc:
            continue

        # Identify the part type from the description prefix
        prefix = _extract_bom_prefix(desc)
        if not prefix:
            # Cannot identify part type — skip (fasteners, keys, etc.)
            continue

        canonical = nom.resolve(prefix) or _try_partial_resolve(prefix, nom)
        if not canonical:
            # Try resolving by the full description
            canonical = nom.resolve(desc) or _try_partial_resolve(desc, nom)

        if not canonical:
            unresolved.append({
                "source":        "bom",
                "original_name": desc,
                "item_number":   entry.get("item_number"),
            })
            continue

        # Extract material and coating from the description string
        material, has_coating = _extract_material_from_bom_desc(desc)

        # Only store first occurrence per canonical name
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
    """
    Normalize SAP data → (parts, unresolved, metadata).

    sap_data shape: {entries: [{key, value}], design_text: str}

    Strategy: try to resolve each entry's key via nomenclature.
      - Resolved   → part-material entry
      - Unresolved → metadata (pump specs, config values)

    Returns:
        parts    : {canonical_name: {present, material, coating}}
        unresolved: [{source, original_name}]
        metadata : {key: value}  — pump specs for LLM prompt and report
    """
    parts      = {}
    unresolved = []
    metadata   = {}

    entries = sap_data.get("entries", [])

    for entry in entries:
        key   = (entry.get("key")   or "").strip()
        value = (entry.get("value") or "")
        if not key or value is None:
            continue

        # Skip entries with no meaningful value
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
            # Non-part entry → metadata
            metadata[key] = value_str

    return parts, unresolved, metadata


# ── Part Context Builder ───────────────────────────────────────────────────

def _build_part_context(
    canonical: str,
    cs_parts: dict,
    bom_parts: dict,
    sap_parts: dict,
) -> dict:
    cs  = cs_parts.get(canonical)
    bom = bom_parts.get(canonical)
    sap = sap_parts.get(canonical)

    sources    = {"cs": cs, "bom": bom, "sap": sap}
    present_in = [s for s, d in sources.items() if d]
    missing_from = [s for s, d in sources.items() if not d]

    # Materials
    raw_materials = {
        s: d["material"]
        for s, d in sources.items()
        if d and d.get("material")
    }
    coating_flags = {}
    for s, d in sources.items():
        if not d:
            continue
        if s == "cs":
            coating_flags[s] = "COATING" in (d.get("material") or "").upper()
        else:
            coating_flags[s] = bool(d.get("coating", False))

    # Rigid material comparison
    if len(raw_materials) >= 2:
        mat_comparison = rigid_materials_match(raw_materials, coating_flags)
        mat_comparison["method"] = "rigid"
    elif len(raw_materials) == 1:
        mat_comparison = {
            "method": "rigid", "result": "INSUFFICIENT",
            "normalized": {}, "coating": coating_flags,
            "coating_match": None,
            "explanation": "Only one source has material data",
        }
    else:
        mat_comparison = {
            "method": "rigid", "result": "INSUFFICIENT",
            "normalized": {}, "coating": coating_flags,
            "coating_match": None,
            "explanation": "No material data available",
        }

    # Quantities
    quantities = {}
    for s, d in sources.items():
        if not d:
            continue
        qty = d.get("qty") or d.get("quantity")
        if qty is not None and str(qty).upper() != "AS REQD":
            try:
                quantities[s] = float(qty)
            except (ValueError, TypeError):
                pass

    qty_match    = len(set(quantities.values())) <= 1 if len(quantities) >= 2 else True
    coating_match = mat_comparison.get("coating_match")
    if coating_match is None:
        coating_match = True

    return {
        "canonical_name":    canonical,
        "cs":                cs,
        "bom":               bom,
        "sap":               sap,
        "present_in":        present_in,
        "missing_from":      missing_from,
        "material_comparison": mat_comparison,
        "quantities":        quantities,
        "qty_match":         qty_match,
        "coating_flags":     coating_flags,
        "coating_match":     coating_match,
        "discrepancies":     [],
    }


def _is_fully_clear(ctx: dict) -> bool:
    if ctx["missing_from"]:
        return False
    if ctx["material_comparison"]["result"] != "MATCH":
        return False
    if not ctx["qty_match"]:
        return False
    if not ctx["coating_match"]:
        return False
    return True


def _clean_for_output(part: dict) -> dict:
    return {
        "canonical_name":    part["canonical_name"],
        "cs":                part["cs"],
        "bom":               part["bom"],
        "sap":               part["sap"],
        "material_comparison": part["material_comparison"],
        "discrepancies":     part["discrepancies"],
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
    """
    Format one part's data for the LLM prompt.

    Produces a structured, information-dense block that mirrors the prompt's
    column descriptions so the LLM can apply its rules without ambiguity.
    """
    name  = part["canonical_name"]
    lines = [f"{index}. {name}"]

    # ── Per-source data ────────────────────────────────────────────────
    for src in ("cs", "bom", "sap"):
        entry = part.get(src)
        if not entry:
            lines.append(f"   {src.upper()}: absent")
            continue

        mat     = entry.get("material")
        qty     = entry.get("qty") or entry.get("quantity")
        coating = entry.get("coating", False)

        mat_str = f'"{mat}"' if mat else "null (not printed in drawing)" if src == "cs" else "not specified"
        qty_str = str(int(qty) if isinstance(qty, float) and qty == int(qty) else qty) if qty else ("not available — SAP has no qty" if src == "sap" else "not specified")
        coat_str = " | coating: YES" if coating else ""

        lines.append(f"   {src.upper()}: material={mat_str} | qty={qty_str}{coat_str}")

    # ── Rigid check result ─────────────────────────────────────────────
    rigid  = part["material_comparison"]
    result = rigid["result"]

    if result == "MATCH":
        lines.append(f"   Rigid check: MATCH — {rigid.get('explanation', 'normalised codes agree')}")

    elif result == "MISMATCH":
        normalized = rigid.get("normalized", {})
        # Show only sources that have material data
        norm_parts = [f"{s}→{v}" for s, v in normalized.items() if v]
        lines.append(
            f"   Rigid check: MISMATCH — normalizer could not reconcile: "
            f"{', '.join(norm_parts) if norm_parts else 'see raw values above'}"
        )

    else:  # INSUFFICIENT
        have_mat = [s for s in ("cs","bom","sap") if (part.get(s) or {}).get("material")]
        lines.append(
            f"   Rigid check: INSUFFICIENT — material data only in: "
            f"{', '.join(have_mat) if have_mat else 'none'}"
        )

    # ── Quantity note ──────────────────────────────────────────────────
    if not part["qty_match"] and part["quantities"]:
        qty_vals = {
            s: (int(q) if q == int(q) else q)
            for s, q in part["quantities"].items()
        }
        lines.append(
            f"   Qty note: values differ — "
            + ", ".join(f"{s}={q}" for s, q in qty_vals.items())
        )

    # ── Coating note ───────────────────────────────────────────────────
    if not part["coating_match"]:
        coat_vals = {s: v for s, v in part.get("coating_flags", {}).items()}
        lines.append(
            f"   Coating note: flags differ — "
            + ", ".join(f"{s}={'YES' if v else 'NO'}" for s, v in coat_vals.items())
        )

    return "\n".join(lines)


def _apply_llm_results(parts: list[dict], llm_results: list[dict]):
    llm_by_name = {r.get("part", ""): r for r in llm_results}

    for part in parts:
        llm_result = llm_by_name.get(part["canonical_name"])
        if not llm_result:
            logger.warning(
                f"LLM returned no result for '{part['canonical_name']}' — fallback"
            )
            _fallback_flag_single(part)
            continue

        status       = llm_result.get("status", "CLEAR")
        explanation  = llm_result.get("explanation", "")
        discrepancies = llm_result.get("discrepancies", [])

        part["material_comparison"]["method"]      = "llm"
        part["material_comparison"]["explanation"] = explanation

        if status == "FLAGGED" and discrepancies:
            if any(d.get("type") == "MATERIAL_MISMATCH" for d in discrepancies):
                part["material_comparison"]["result"] = "MISMATCH"
            for disc in discrepancies:
                part["discrepancies"].append({
                    "type":   disc.get("type", "UNKNOWN"),
                    "reason": disc.get("reason", "Flagged by AI evaluation"),
                    "detail": disc.get("reason", ""),
                })
        else:
            if part["material_comparison"]["result"] == "MISMATCH":
                part["material_comparison"]["result"] = "MATCH"
            part["discrepancies"] = []


def _fallback_flag_all(parts: list[dict]):
    for part in parts:
        _fallback_flag_single(part)


def _fallback_flag_single(part: dict):
    part["material_comparison"]["method"] = "fallback"
    part["material_comparison"]["explanation"] = (
        "AI evaluation unavailable — flagged for manual review"
    )
    if part["missing_from"]:
        part["discrepancies"].append({
            "type":   "MISSING",
            "reason": f"Part not found in {', '.join(part['missing_from'])}",
            "detail": f"Present in {', '.join(part['present_in'])} but missing from {', '.join(part['missing_from'])}",
        })
    if part["material_comparison"]["result"] == "MISMATCH":
        mats = {
            s: (part.get(s) or {}).get("material", "")
            for s in ("cs", "bom", "sap")
            if (part.get(s) or {}).get("material")
        }
        part["discrepancies"].append({
            "type":   "MATERIAL_MISMATCH",
            "reason": f"Material may differ: {', '.join(f'{s}: {m}' for s,m in mats.items())}",
            "detail": str(mats),
        })
    if not part["qty_match"] and part["quantities"]:
        qty_str = ", ".join(
            f"{s}: {int(q) if q == int(q) else q}"
            for s, q in part["quantities"].items()
        )
        part["discrepancies"].append({
            "type":   "QUANTITY_MISMATCH",
            "reason": f"Quantity mismatch: {qty_str}",
            "detail": qty_str,
        })
    if not part["coating_match"]:
        part["discrepancies"].append({
            "type":   "COATING_MISMATCH",
            "reason": "Coating specification differs between documents",
            "detail": "Coating flags differ across sources",
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


# ── BOM description helpers ────────────────────────────────────────────────

def _extract_bom_prefix(description: str) -> str | None:
    """Match the known part-abbreviation prefix at the start of a BOM description."""
    upper = description.upper()
    for prefix in _BOM_PART_PREFIXES:
        if upper.startswith(prefix):
            return prefix
    return None


def _extract_material_from_bom_desc(desc: str) -> tuple[str | None, bool]:
    """
    Extract material code and coating flag from a raw BOM description string.

    BOM descriptions follow the pattern:
        [PART_ABBREV] [internal codes] [MATERIAL_CODE][+COAT optional]
    The material is always the last meaningful token(s).
    """
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

    # Special cases
    if "GRAPHITED" in upper and "COTTON" in upper:
        return "GRAPHITED COTTON", False
    if "NITRILE" in upper:
        return "NITRILE RUBBER", False

    return None, has_coating


# ── Shared helpers ─────────────────────────────────────────────────────────

def _is_fastener_or_generic(desc: str) -> bool:
    """
    Return True for parts excluded from cross-source comparison:
    1. Fasteners/hardware — never in SAP, always generate false MISSING flags.
    2. Consumables — Gland Packing (graphited cotton) is a wear item not a
       structural MOC part. Its CS material can be incorrectly inherited by
       adjacent rows (e.g. Loose Stuffing Box ref 2401) via Gemini spanning
       cell errors, causing false MATERIAL_MISMATCH flags.
    """
    upper = desc.upper()
    keywords = [
        # Fasteners / hardware
        "FASTNER", "FASTENER", "GASKET", "O' RING", "O RING", "'O' RING",
        "WASHER", "STUD", "HEX NUT", "HEX HD SCR", "SOC SET SCR",
        "SOC HD CAP", "HEX PLUG", "DOWEL PIN", "RIVET", "ERECTION PACKER",
        "FOUNDATION BOLT", "NAME PLATE", "INDICATOR ARROW", "CORD ",
        "BES KEY", "S-BER KEY", "KEY ", " KEY",
        # Consumables
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