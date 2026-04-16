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
           engineering discrepancies vs. expected behavior (source scope
           differences, multi-stage quantity multipliers, notation variants).

The Nomenclature class manages the alias map for part name resolution.
When users reject a false-positive discrepancy, the alias is added so
future comparisons resolve correctly.
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


# ── Gemini LLM Prompt ──────────────────────────────────────────────────────

LLM_EVALUATION_PROMPT = """\
You are an expert in engineering materials, metallurgy, and industrial vertical turbine pump components.

You are reviewing a cross-reference comparison of parts extracted from three engineering document sources:
- **CS**: Cross-Section engineering drawing — shows ALL components including installation hardware, accessories, and site items.
- **BOM**: Bill of Materials Excel — lists manufactured and purchased components with total quantities.
- **SAP**: SAP enterprise system data — tracks only major structural/rotating components (typically 15-20 key parts).

## Pump Details
{pump_info}

## Your Task
For each part below, determine whether there are **genuine engineering discrepancies** that would concern a procurement or manufacturing engineer. Most potential issues will be EXPECTED behavior, not real errors.

## What is NORMAL (do NOT flag these):

**Missing parts:**
- SAP only tracks major components (impellers, shafts, diffusers, bearing sleeves, wear rings, column pipes, bell mouths, etc.). Installation items, accessories, hardware, and auxiliary parts (foundation bolts, erection packers, keys, oil indicators, cooling coils, sole plates, ratchets, sand collars, split collars, lock nuts, sleeve nuts, gland packing, water deflectors, thrust blocks, thrust bearings, logging rings, oil retainer bushes, flexible couplings, alignment pads, adjusting rings, split glands, loose stuffing boxes, distance sleeves, bearing bush carriers, etc.) being absent from SAP is EXPECTED.
- BOM may not include site/installation items (foundation bolts, erection packers, etc.) or accessories.
- CS drawings show everything — parts present ONLY in CS are often accessories or installation items. This is normal.
- A part present in only one or two sources is only a real MISSING discrepancy if it is a MAJOR structural or rotating component that the other source should definitely track.

**Material notation differences:**
- "ASTM A276 GR SS410" and "SS410" are the SAME material (full specification vs short code).
- "M.S. IS:2062 GR-B" and "MS" are the SAME material (Mild Steel with specification prefix).
- "CI IS 210 GR FG260" and "FG260" are the SAME material (Cast Iron with specification prefix).
- Composite materials like "CUTLESS RUBBER + SS410 SHELL" matching "SS410" is expected — the primary structural material matches.
- Cast/wrought equivalents of the same alloy are the same base material (e.g., CA6NM cast and wrought forms).
- Heat treatment suffixes (T condition, H, etc.) do not change the base alloy identity.
- "FORGED STEEL" is a general term — if no other source has specific data, this is not a mismatch.
- "MFG.STD" or "M&P Std" means manufacturer standard — not a material specification, skip comparison.

**Quantity differences in multi-stage pumps:**
- This pump has {stages} stage(s). For per-stage components (diffusers, impellers, wear rings, bearing sleeves, muff couplings, intermediate shafts, intermediate bearing sleeves, RM pipes), BOM lists TOTAL quantity across all stages while CS shows PER-STAGE or PER-UNIT quantity.
- BOM qty being a multiple of CS qty (approximately CS qty × stages, with some variance for design specifics or spares) is EXPECTED, not an error.
- Single-instance components (suction bell mouth, sole plate, bearing housing, delivery bend, top shaft) should have matching quantities.

**Coating:**
- Minor coating notation differences are not significant if the base material matches.
- One source saying "GGG50 + COATING" and another saying "GGG50 + COATING" is a match even with slight notation variance.

## What IS a real discrepancy (DO flag these):
- **MATERIAL_MISMATCH**: Materials from different sources are genuinely different alloy families (e.g., stainless steel vs cast iron, CA15 vs FG260, SS410 vs HTS).
- **MISSING**: A major structural/rotating component (impeller, shaft, diffuser, bearing housing, wear ring, etc.) that SHOULD be tracked in a source but is genuinely absent.
- **QUANTITY_MISMATCH**: Quantities that CANNOT be explained by multi-stage multiplication or standard engineering practice.
- **COATING_MISMATCH**: One source explicitly requires coating while another explicitly specifies no coating for a part where coating is structurally significant.

## Parts to Evaluate

{parts_text}

## Response Format
Return ONLY a valid JSON array. No markdown formatting, no code fences, no extra text.
For each part return an object with these exact keys:
- "part": the canonical part name (string, must match exactly)
- "status": "CLEAR" if no real discrepancies, "FLAGGED" if there are genuine issues
- "discrepancies": array of confirmed issues (empty array for CLEAR parts), each with "type" (MATERIAL_MISMATCH, MISSING, QUANTITY_MISMATCH, or COATING_MISMATCH) and "reason" (concise 1-line explanation)
- "explanation": brief 1-line summary of your evaluation
"""


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


# ── Main Comparison Entry Point ─────────────────────────────────────────────

def compare(identifier: str, processed_dir: Path) -> dict:
    """
    Compare parts across CS, BOM, and SAP extracted data using two-pass logic.

    Pass 1: Rigid string normalization — clears parts where everything matches.
    Pass 2: Gemini LLM evaluation — all remaining parts get full AI review
            covering MISSING, MATERIAL, QUANTITY, and COATING discrepancies.

    Returns a comparison results dict with per-part info and flagged discrepancies.
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

    # ── Phase 1: Build context for every part ──────────────────────────────
    all_parts = []
    for canonical in all_canonical:
        ctx = _build_part_context(canonical, cs_parts, bom_parts, sap_parts)
        all_parts.append(ctx)

    # ── Phase 2: Rigid pass — clear obvious full matches ───────────────────
    clear_parts = []
    needs_llm = []
    for ctx in all_parts:
        if _is_fully_clear(ctx):
            ctx["discrepancies"] = []
            clear_parts.append(ctx)
        else:
            needs_llm.append(ctx)

    logger.info(
        f"Rigid pass: {len(clear_parts)} clear, {len(needs_llm)} need LLM review"
    )

    # ── Phase 3: LLM evaluation of all non-clear parts ────────────────────
    if needs_llm:
        sap_metadata = sap_data.get("metadata", {})
        _llm_evaluate_all(needs_llm, sap_metadata)

    # ── Assemble final results ─────────────────────────────────────────────
    parts_comparison = sorted(
        [_clean_for_output(p) for p in clear_parts + needs_llm],
        key=lambda p: p["canonical_name"],
    )

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


# ── Part Context Builder ───────────────────────────────────────────────────

def _build_part_context(
    canonical: str,
    cs_parts: dict,
    bom_parts: dict,
    sap_parts: dict,
) -> dict:
    """Gather all comparison info for one canonical part without making
    discrepancy decisions. Decisions are deferred to the LLM pass."""
    cs = cs_parts.get(canonical)
    bom = bom_parts.get(canonical)
    sap = sap_parts.get(canonical)

    sources = {"cs": cs, "bom": bom, "sap": sap}
    present_in = [name for name, data in sources.items() if data]
    missing_from = [name for name, data in sources.items() if not data]

    # ── Material info ──────────────────────────────────────────────────
    raw_materials = {}
    coating_flags = {}
    for name, data in sources.items():
        if data and data.get("material"):
            raw_materials[name] = data["material"]
        if data:
            if name == "cs":
                mat = (data.get("material") or "").upper()
                coating_flags[name] = "COATING" in mat
            else:
                coating_flags[name] = bool(data.get("coating", False))

    # Rigid material comparison
    mat_comparison = {"method": "rigid", "result": "MATCH", "explanation": None}
    if len(raw_materials) >= 2:
        rigid_result = rigid_materials_match(raw_materials, coating_flags)
        mat_comparison = {
            "method": "rigid",
            "result": rigid_result["result"],
            "normalized": rigid_result.get("normalized", {}),
            "coating": coating_flags,
            "coating_match": rigid_result.get("coating_match"),
            "explanation": rigid_result.get("explanation"),
        }
    elif len(raw_materials) == 1:
        mat_comparison = {
            "method": "rigid",
            "result": "INSUFFICIENT",
            "normalized": {},
            "coating": coating_flags,
            "coating_match": None,
            "explanation": "Only one source has material data",
        }
    else:
        mat_comparison = {
            "method": "rigid",
            "result": "INSUFFICIENT",
            "normalized": {},
            "coating": coating_flags,
            "coating_match": None,
            "explanation": "No material data available",
        }

    # ── Quantity info ──────────────────────────────────────────────────
    quantities = {}
    for name, data in sources.items():
        if data:
            qty = data.get("qty") or data.get("quantity")
            if qty is not None and str(qty).upper() != "AS REQD":
                try:
                    quantities[name] = float(qty)
                except (ValueError, TypeError):
                    pass

    qty_match = True
    if len(quantities) >= 2:
        if len(set(quantities.values())) > 1:
            qty_match = False

    coating_match = mat_comparison.get("coating_match")
    if coating_match is None:
        coating_match = True

    return {
        "canonical_name": canonical,
        "cs": cs,
        "bom": bom,
        "sap": sap,
        "present_in": present_in,
        "missing_from": missing_from,
        "material_comparison": mat_comparison,
        "quantities": quantities,
        "qty_match": qty_match,
        "coating_flags": coating_flags,
        "coating_match": coating_match,
        "discrepancies": [],
    }


def _is_fully_clear(ctx: dict) -> bool:
    """Check if rigid pass can definitively clear this part (no ambiguity).

    A part is clear only when:
    - Present in all 3 sources
    - Materials match rigidly
    - Quantities match
    - Coating matches
    """
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
    """Remove internal processing fields before saving/returning."""
    return {
        "canonical_name": part["canonical_name"],
        "cs": part["cs"],
        "bom": part["bom"],
        "sap": part["sap"],
        "material_comparison": part["material_comparison"],
        "discrepancies": part["discrepancies"],
    }


# ── Pass 2: LLM Full Evaluation ───────────────────────────────────────────

def _llm_evaluate_all(parts: list[dict], sap_metadata: dict):
    """Send all non-clear parts to Gemini for comprehensive evaluation.

    The LLM decides which potential issues are genuine engineering
    discrepancies vs. expected behavior. Mutates parts in-place.
    """
    if not config.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set — falling back to conservative flagging")
        _fallback_flag_all(parts)
        return

    # Extract pump info from SAP metadata
    stages = sap_metadata.get("No of Stages", "unknown")
    pump_name = sap_metadata.get("VT pump Common Name", "Unknown Pump")
    pump_info_lines = [f"Pump: {pump_name}", f"Number of stages: {stages}"]
    for key in ("Liquid Handled", "Full Load Speed (RPM)", "MOC category",
                "Type of Sealing", "Scope of Supply"):
        if key in sap_metadata:
            pump_info_lines.append(f"{key}: {sap_metadata[key]}")
    pump_info = "\n".join(pump_info_lines)

    # Format each part for the prompt
    parts_text_blocks = []
    for i, part in enumerate(parts, 1):
        parts_text_blocks.append(_format_part_for_prompt(i, part))

    prompt = LLM_EVALUATION_PROMPT.format(
        pump_info=pump_info,
        stages=stages,
        parts_text="\n\n".join(parts_text_blocks),
    )

    # Call Gemini
    llm_results = _call_gemini_with_retry(prompt, retries=2)

    if llm_results is None:
        logger.error("LLM evaluation failed — falling back to conservative flagging")
        _fallback_flag_all(parts)
        return

    # Apply LLM results
    _apply_llm_results(parts, llm_results)


def _format_part_for_prompt(index: int, part: dict) -> str:
    """Format one part's context for the LLM prompt."""
    lines = [f"{index}. Part: \"{part['canonical_name']}\""]

    for src in ("cs", "bom", "sap"):
        entry = part.get(src)
        if entry and entry.get("present"):
            mat = entry.get("material") or "not specified"
            qty = entry.get("qty") or entry.get("quantity") or "not specified"
            coating = entry.get("coating", False)
            line = f"   {src.upper()}: Present | Material: \"{mat}\" | Qty: {qty}"
            if coating:
                line += " | Coating: Yes"
            lines.append(line)
        else:
            lines.append(f"   {src.upper()}: Not present")

    # Add rigid analysis hint
    rigid = part["material_comparison"]
    if rigid["result"] == "MATCH":
        lines.append(f"   Rigid check: Materials MATCH — {rigid.get('explanation', '')}")
    elif rigid["result"] == "MISMATCH":
        normalized = rigid.get("normalized", {})
        norm_str = ", ".join(f"{s}={v}" for s, v in normalized.items())
        lines.append(f"   Rigid check: Materials MISMATCH (normalized: {norm_str})")
    else:
        lines.append(f"   Rigid check: Insufficient material data to compare")

    if not part["qty_match"] and part["quantities"]:
        qty_str = ", ".join(
            f"{s}: {int(q) if q == int(q) else q}"
            for s, q in part["quantities"].items()
        )
        lines.append(f"   Quantities differ: {qty_str}")

    if not part["coating_match"]:
        lines.append(f"   Coating differs across sources")

    return "\n".join(lines)


def _apply_llm_results(parts: list[dict], llm_results: list[dict]):
    """Map LLM evaluation results back to part objects. Mutates in-place."""
    # Build lookup by part name
    llm_by_name = {}
    for result in llm_results:
        llm_by_name[result.get("part", "")] = result

    for part in parts:
        llm_result = llm_by_name.get(part["canonical_name"])

        if not llm_result:
            logger.warning(
                f"LLM returned no result for '{part['canonical_name']}' — "
                "applying conservative fallback"
            )
            _fallback_flag_single(part)
            continue

        status = llm_result.get("status", "CLEAR")
        explanation = llm_result.get("explanation", "")
        discrepancies = llm_result.get("discrepancies", [])

        # Update material comparison with LLM evaluation
        part["material_comparison"]["method"] = "llm"
        part["material_comparison"]["explanation"] = explanation

        if status == "FLAGGED" and discrepancies:
            # Check if there's a material mismatch among the discrepancies
            has_material_issue = any(
                d.get("type") == "MATERIAL_MISMATCH" for d in discrepancies
            )
            if has_material_issue:
                part["material_comparison"]["result"] = "MISMATCH"
            # Keep rigid result otherwise (MATCH, INSUFFICIENT, etc.)

            # Add confirmed discrepancies
            for disc in discrepancies:
                part["discrepancies"].append({
                    "type": disc.get("type", "UNKNOWN"),
                    "reason": disc.get("reason", "Flagged by AI evaluation"),
                    "detail": disc.get("reason", ""),
                })
        else:
            # LLM says CLEAR — no discrepancies
            if part["material_comparison"]["result"] == "MISMATCH":
                # LLM overrides rigid mismatch — materials actually match
                part["material_comparison"]["result"] = "MATCH"
            part["discrepancies"] = []


def _fallback_flag_all(parts: list[dict]):
    """When LLM is unavailable, apply conservative flagging for all parts."""
    for part in parts:
        _fallback_flag_single(part)


def _fallback_flag_single(part: dict):
    """Conservative fallback for a single part — flags all potential issues."""
    part["material_comparison"]["method"] = "fallback"
    part["material_comparison"]["explanation"] = (
        "AI evaluation unavailable — flagged for manual review"
    )

    # Flag MISSING
    if part["missing_from"]:
        part["discrepancies"].append({
            "type": "MISSING",
            "reason": f"Part not found in {', '.join(part['missing_from'])} document(s)",
            "detail": f"Present in {', '.join(part['present_in'])} but missing from {', '.join(part['missing_from'])}",
        })

    # Flag material mismatch if rigid said so
    if part["material_comparison"]["result"] == "MISMATCH":
        mats = {}
        for src in ("cs", "bom", "sap"):
            entry = part.get(src)
            if entry and entry.get("material"):
                mats[src] = entry["material"]
        parts_str = ", ".join(f"{s}: {m}" for s, m in mats.items())
        part["discrepancies"].append({
            "type": "MATERIAL_MISMATCH",
            "reason": f"Material may differ: {parts_str} (manual review needed)",
            "detail": parts_str,
        })

    # Flag quantity mismatch
    if not part["qty_match"] and part["quantities"]:
        qty_str = ", ".join(
            f"{s}: {int(q) if q == int(q) else q}"
            for s, q in part["quantities"].items()
        )
        part["discrepancies"].append({
            "type": "QUANTITY_MISMATCH",
            "reason": f"Quantity mismatch: {qty_str}",
            "detail": qty_str,
        })

    # Flag coating mismatch
    if not part["coating_match"]:
        part["discrepancies"].append({
            "type": "COATING_MISMATCH",
            "reason": "Coating specification differs between documents",
            "detail": "Coating flags differ across sources",
        })


def _call_gemini_with_retry(prompt: str, retries: int = 2) -> list | None:
    """Call Gemini API with retry logic. Returns parsed JSON list or None."""
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
            # Clean markdown fences if present despite JSON mime type
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


# ── Normalization helpers ────────────────────────────────────────────────────

def _normalize_cs(cs_data: list, nom: Nomenclature) -> tuple[dict, list]:
    """Normalize CS BOM entries. Returns (resolved_parts, unresolved_list)."""
    parts = {}
    unresolved = []

    for entry in cs_data:
        desc = (entry.get("description") or "").strip()
        if not desc:
            continue

        if desc.upper() in ("DESCRIPTION", "REF.", "MATERIAL."):
            continue

        if _is_fastener_or_generic(desc):
            continue

        canonical = nom.resolve(desc)
        if not canonical:
            canonical = _try_partial_resolve(desc, nom)

        if canonical:
            if canonical not in parts:
                parts[canonical] = {
                    "present": True,
                    "material": entry.get("material"),
                    "qty": entry.get("qty"),
                }
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

    For 'agree' decisions: mark discrepancy as confirmed (real error).
    For 'disagree' decisions: update nomenclature with new alias mapping.

    Returns updated validation status.
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
            # Find the discrepancy to capture its reason
            reason = None
            for part in results.get("parts", []):
                if part["canonical_name"] == canonical:
                    discs = part.get("discrepancies", [])
                    if disc_index < len(discs):
                        reason = discs[disc_index].get("reason")
                    break

            confirmed.append({
                "canonical_name": canonical,
                "discrepancy_index": disc_index,
                "reason": reason,
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
