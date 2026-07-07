"""
Part comparison engine for the BOM Components Validator — rebuilt.

Two stages instead of the old rigid+Gemini tangle:

  STAGE 1 — Deterministic (unchanged philosophy, kept lean)
    Name-match parts across CS / BOM / SAP via the nomenclature resolver, then
    compare materials with materials.py (family equivalence + coating + the
    SAP > CS > BOM authority model). A part is CLEARED here — no LLM — only when
    it is fully consistent: materials agree (or nothing to compare) AND there is
    no presence concern given each document's purpose. Target: ~90% cleared,
    identical on every run.

  STAGE 2 + 3 — One rich GPT-5.5 call (only the leftovers)
    Everything Stage 1 could not clear — material mismatches, presence
    asymmetries, and the unresolved (name-unmatched) items from all three
    sources — goes to a single, exhaustive GPT-5.5 call that:
      • reconciles unmatched names that refer to the SAME physical part
        (proposals only; they become permanent aliases only when a human
         agrees, via apply_validation);
      • judges PRESENCE discrepancies using document PURPOSE, not naive diffing:
          - BOM is the superset — anything real should be in BOM.
          - CS ⊆ BOM — CS holds integral assembled parts; a CS part absent
            from BOM is a flag.
          - SAP carries only its fixed keyed characteristics — absence outside
            that set is NEVER a flag.
          - "integral" = wetted/structural path (from nomenclature 'type').
      • judges MATERIAL discrepancies with authority SAP > CS > BOM.
    It is GROUNDED: it only judges extractor-provided values; it never invents
    a material. If the API is unavailable, a conservative deterministic fallback
    flags unresolved mismatches for manual review (never silently clears).

The output contract (comparison_results.json), the unresolved list, and the
apply_validation alias-learning flow are unchanged.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from backend import config
from backend.materials import (
    is_consumable_material,
    is_coating_brand,
    load_part_type_sets,
    rigid_materials_match,
    MAJOR_WETTED_PARTS,
    STRUCTURAL_PART_NAMES,
)

logger = logging.getLogger(__name__)

NOMENCLATURE_PATH = config.BACKEND_DIR / "nomenclature.json"

# ── SAP domain knowledge ────────────────────────────────────────────────────
# SAP is a customer sales/design note. Its material characteristics are a FIXED
# vocabulary of function-named keys. A canonical part is "expected in SAP" only
# if it resolves from one of these keys; absence of anything else from SAP is
# never a discrepancy. (Resolved to canonical names at runtime via nomenclature.)
SAP_MATERIAL_KEYS = [
    "Suc Bell Mouth", "Diffuser Moc", "Strainer", "Impeller", "Imp Wear Ring",
    "Neck Ring", "Shaft", "Top Shaft", "Int Shaft", "Pump Brg Sleeve",
    "Int Sleeve", "Delivery Bend / Tee", "Motor Stool", "Gland", "Gland Sleeve",
    "Muff Coupling", "Column Pipe", "Logging Ring", "Bearing bush",
    "St Box Packing", "Coupling Moc", "Non Wetted Fasteners", "Wetted Fasteners",
]
# SAP values that are placeholders, not real materials — treated as "not specified".
SAP_PLACEHOLDER_VALUES = {
    "NOT APPLICABLE", "NA", "N/A", "M&P STANDARD", "M&P STD", "M&P",
    "STANDARD", "NONE", "-", "",
}

# ── BOM description: part prefix abbreviations (longest first) ───────────────
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


# ═══════════════════════════════════════════════════════════════════════════
# LLM prompt (Stages 2+3) — one exhaustive call
# ═══════════════════════════════════════════════════════════════════════════

LLM_SYSTEM = """You are a senior vertical-turbine-pump engineer and metallurgist auditing \
part data across three DIFFERENT documents for ONE pump. The documents exist for different \
purposes and are NOT expected to be identical. Your job is to flag only GENUINE discrepancies.

DOCUMENT PURPOSES (this governs presence judgments):
- BOM  : the manufacturing bill of materials. The SUPERSET — every real part, large or
         small, should appear here. Missing-from-BOM is the strongest signal.
- CS   : the cross-section engineering drawing. Holds the pump's INTEGRAL assembled parts
         (wetted/structural path). Rule: CS ⊆ BOM. A CS part absent from BOM is a flag.
- SAP  : a customer sales/design note. Carries ONLY a fixed set of keyed characteristic
         materials (the ~20 "sap_expected" parts). It is NOT a parts list. A part being
         absent from SAP is NEVER a discrepancy unless that part is one SAP is expected to
         carry (sap_expected=true).

TWO DISCREPANCY TYPES:
1. MATERIAL_MISMATCH — the same part appears in 2+ docs but the base ALLOY FAMILIES
   genuinely differ (after the equivalences below). Authority for material truth:
   SAP > CS > BOM. Identify which document holds the wrong value.
2. MISSING — a part is absent from a document whose PURPOSE says it should be there
   (integral part missing from CS or BOM; a part in CS missing from BOM). Never flag an
   absence that is natural to a document's purpose.

MATERIAL EQUIVALENCE (already applied deterministically — do NOT re-flag these):
  SS410 = SS410T = SS410H = CA15 ;  CF8M = CF3M = SS316 ;
  MS = M.S. = WCB = IS:2062/E250 ;  FG260 = CI IS 210 GR FG260 = CI ;
  CUTLESS RUBBER + SS410 = SS410 (composite: shell alloy governs).
  Coating (+ COATING) is NOT a material difference when coating_required=true.
  "Forged Steel" in SAP for a coupling/shaft is a known generic configurator entry — treat
  as compatible with SS410, NOT a mismatch.
Only flag when the BASE ALLOY FAMILIES are truly different (e.g. SS410 vs HTS, FG260 vs MS,
CA6NM vs GGG50, CF8M vs SS410 where not a documented dual-spec).

NAME RECONCILIATION:
  Some leftover names did not match by dictionary. Using pump-domain knowledge, decide which
  refer to the SAME physical part across documents despite different naming (e.g. CS
  "DELIVERY BEND & MOTOR STOOL" ≈ BOM "DBMS" ≈ SAP "Delivery Bend / Tee" + "Motor Stool").
  Propose these as reconciliations. They are PROPOSALS for human confirmation, not facts.

GROUNDING RULES (critical):
  - Judge ONLY the materials/values given to you. NEVER invent a material.
  - correct_material must be one of the values actually present for that part, or null.
  - Default bias is CLEAR. Flag only genuine engineering problems.
"""

LLM_INSTRUCTIONS = """Return ONLY a JSON object (no prose, no code fences) with this shape:
{
  "verdicts": [
    {
      "part": "<canonical part name, exactly as given>",
      "status": "CLEAR" | "FLAGGED",
      "authority": "CS" | "SAP" | "BOM" | "CS+SAP" | "SAP+BOM" | "CS+BOM" | "MANUAL_REVIEW",
      "correct_material": "<a value present for this part, or null>",
      "discrepancies": [
        {"type": "MATERIAL_MISMATCH" | "MISSING",
         "source_in_error": "CS" | "BOM" | "SAP" | "UNKNOWN",
         "reason": "<one concise sentence naming what is wrong and in which document>"}
      ],
      "explanation": "<one sentence: why CLEAR, or the real problem>"
    }
  ],
  "reconciliations": [
    {"canonical": "<existing canonical name or best label>",
     "same_as": [{"source": "cs|bom|sap", "name": "<unresolved name>"}],
     "confidence": "high" | "medium" | "low",
     "reason": "<why these are the same physical part>"}
  ]
}
Rules: discrepancies=[] when status=CLEAR. Every evaluated part needs a verdict.
"""


# ═══════════════════════════════════════════════════════════════════════════
# Nomenclature
# ═══════════════════════════════════════════════════════════════════════════

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

    def _build_reverse_map(self) -> dict:
        rev = {}
        for canonical, info in self.data.items():
            rev[canonical.upper()] = canonical
            for alias in info.get("aliases", []):
                rev[alias.upper()] = canonical
        return rev

    def resolve(self, name: str):
        if not name:
            return None
        return self._reverse.get(name.strip().upper())

    def add_alias(self, canonical: str, new_alias: str):
        if canonical not in self.data:
            self.data[canonical] = {"aliases": []}
        aliases = self.data[canonical].setdefault("aliases", [])
        if new_alias not in aliases:
            aliases.append(new_alias)
            self._reverse[new_alias.upper()] = canonical
            self._save()
            logger.info(f"Added alias '{new_alias}' -> '{canonical}'")

    def get_all_canonical(self) -> list:
        return sorted(self.data.keys())


# ═══════════════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════════════

def compare(identifier: str, processed_dir: Path) -> dict:
    """Compare parts across CS, BOM, SAP: deterministic Stage 1 + one GPT-5.5 call."""
    nomenclature = Nomenclature()
    load_part_type_sets(nomenclature.path)

    cs_data  = _load_json(processed_dir / "cs_bom.json",   default=[])
    bom_data = _load_json(processed_dir / "bom_data.json", default=[])
    sap_data = _load_json(processed_dir / "sap_data.json", default={})

    cs_parts,  cs_unresolved  = _normalize_cs(cs_data, nomenclature)
    bom_parts, bom_unresolved = _normalize_bom(bom_data, nomenclature)
    sap_parts, sap_unresolved, sap_metadata = _normalize_sap(sap_data, nomenclature)

    coating_required = _resolve_coating_requirement(sap_metadata)

    # canonical part names SAP is EXPECTED to carry (resolve SAP's fixed keys)
    sap_expected = {
        c for c in (nomenclature.resolve(k) for k in SAP_MATERIAL_KEYS) if c
    }

    all_canonical = sorted(
        set(cs_parts) | set(bom_parts) | set(sap_parts)
    )

    # ── Phase 1: build per-part context ───────────────────────────────────
    all_parts = [
        _build_part_context(c, cs_parts, bom_parts, sap_parts,
                            coating_required=coating_required,
                            sap_expected=(c in sap_expected))
        for c in all_canonical
    ]

    # ── Phase 2: Stage 1 deterministic clear ──────────────────────────────
    clear_parts, needs_llm = [], []
    for ctx in all_parts:
        if _stage1_clear(ctx):
            ctx["discrepancies"] = []
            clear_parts.append(ctx)
        else:
            needs_llm.append(ctx)

    logger.info(f"Stage 1: {len(clear_parts)} cleared, {len(needs_llm)} to LLM")

    unresolved = _dedupe_unresolved(cs_unresolved + bom_unresolved + sap_unresolved)

    # ── Phase 3: Stage 2+3 single LLM call ────────────────────────────────
    reconciliations = []
    if needs_llm or unresolved:
        reconciliations = _llm_stage23(needs_llm, unresolved, sap_metadata, sap_expected)

    # ── Assemble output (unchanged contract) ──────────────────────────────
    parts_comparison = sorted(
        [_clean_for_output(p) for p in clear_parts + needs_llm],
        key=lambda p: p["canonical_name"],
    )
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
        "reconciliations": reconciliations,   # LLM name-match proposals (for review)
        "sap_metadata": sap_metadata,
    }

    with open(processed_dir / "comparison_results.json", "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"Comparison complete for {identifier}: "
                f"{len(all_canonical)} parts, {total_discrepancies} discrepancies")
    return results


# ═══════════════════════════════════════════════════════════════════════════
# Normalization  (CS / BOM / SAP  ->  {canonical: {present, material, ...}})
# ═══════════════════════════════════════════════════════════════════════════

def _clean_cs_description(desc: str) -> list:
    candidates = [desc]
    stripped = desc.rstrip(". ").strip()
    if stripped != desc:
        candidates.append(stripped)
    no_paren = re.sub(r"[\s]*[(][^)]*[)]\.?$", "", stripped).strip().rstrip(". ").strip()
    if no_paren and no_paren not in candidates:
        candidates.append(no_paren)
    no_abbrev = re.sub(r"[.][ ]+", " ", no_paren).strip()
    if no_abbrev and no_abbrev not in candidates:
        candidates.append(no_abbrev)
    return candidates


def _normalize_cs(cs_data: list, nom: Nomenclature) -> tuple:
    parts, unresolved = {}, []
    for entry in cs_data:
        desc = (entry.get("description") or "").strip()
        if not desc or desc.upper() in ("DESCRIPTION", "REF.", "MATERIAL."):
            continue
        if _is_fastener_or_generic(desc):
            continue
        canonical = None
        for cand in _clean_cs_description(desc):
            canonical = nom.resolve(cand) or _try_partial_resolve(cand, nom)
            if canonical:
                break
        if canonical:
            if canonical not in parts:
                raw_mat = entry.get("material")
                warning = None
                # CS sanity: consumable/coating brand on a structural part = extraction error
                if canonical in STRUCTURAL_PART_NAMES and raw_mat:
                    if is_consumable_material(raw_mat):
                        warning = (f"CS extraction suspect: '{raw_mat}' is a consumable on "
                                   f"structural part '{canonical}'; excluded from comparison.")
                    elif is_coating_brand(raw_mat):
                        warning = (f"CS extraction suspect: '{raw_mat}' is a coating brand on "
                                   f"structural part '{canonical}'; excluded from comparison.")
                    if warning:
                        logger.warning(warning); raw_mat = None
                parts[canonical] = {"present": True, "material": raw_mat,
                                    "qty": entry.get("qty"), "cs_extraction_warning": warning}
        else:
            unresolved.append({"source": "cs", "original_name": desc, "ref": entry.get("ref")})
    return parts, unresolved


def _normalize_bom(bom_data: list, nom: Nomenclature) -> tuple:
    parts, unresolved = {}, []
    for entry in bom_data:
        desc = (entry.get("description") or "").strip()
        if not desc:
            continue
        prefix = _extract_bom_prefix(desc)
        canonical = None
        if prefix:
            canonical = nom.resolve(prefix) or _try_partial_resolve(prefix, nom)
        if not canonical:
            canonical = nom.resolve(desc) or _try_partial_resolve(desc, nom)
        if not canonical:
            unresolved.append({"source": "bom", "original_name": desc,
                               "item_number": entry.get("item_number")})
            continue
        material, has_coating = _extract_material_from_bom_desc(desc)
        if canonical not in parts:
            parts[canonical] = {"present": True, "material": material,
                                "qty": entry.get("quantity"), "coating": has_coating}
    return parts, unresolved


def _normalize_sap(sap_data: dict, nom: Nomenclature) -> tuple:
    parts, unresolved, metadata = {}, [], {}
    for entry in sap_data.get("entries", []):
        key = (entry.get("key") or "").strip()
        value = entry.get("value")
        if not key or value is None:
            continue
        value_str = str(value).strip()
        if not value_str:
            continue
        canonical = nom.resolve(key)
        if canonical:
            if canonical not in parts:
                # placeholder values are "not specified" — keep present, drop material
                is_placeholder = value_str.upper() in SAP_PLACEHOLDER_VALUES
                has_coating = "COATING" in value_str.upper()
                parts[canonical] = {
                    "present": True,
                    "material": None if is_placeholder else value_str,
                    "raw_material": value_str,
                    "coating": has_coating,
                }
        else:
            metadata[key] = value_str
    return parts, unresolved, metadata


# ═══════════════════════════════════════════════════════════════════════════
# Per-part context + Stage 1 clear
# ═══════════════════════════════════════════════════════════════════════════

def _build_part_context(canonical, cs_parts, bom_parts, sap_parts,
                        coating_required=False, sap_expected=False) -> dict:
    cs, bom, sap = cs_parts.get(canonical), bom_parts.get(canonical), sap_parts.get(canonical)
    raw_materials, coating_flags = {}, {}
    for src, entry in (("cs", cs), ("bom", bom), ("sap", sap)):
        if entry and entry.get("material"):
            raw_materials[src] = entry["material"]
        if entry:
            coating_flags[src] = bool(entry.get("coating", False)) or \
                "COATING" in (entry.get("material") or "").upper()

    if len(raw_materials) >= 2:
        mat = rigid_materials_match(raw_materials, coating_flags, coating_required=coating_required)
        mat["method"] = "rigid"
    else:
        mat = {"method": "rigid", "result": "INSUFFICIENT", "normalized": {}, "families": {},
               "coating_match": None,
               "explanation": ("Only one source has material data" if raw_materials
                               else "No material data available")}
    return {
        "canonical_name": canonical, "cs": cs, "bom": bom, "sap": sap,
        "material_comparison": mat, "coating_required": coating_required,
        "cs_extraction_warning": (cs or {}).get("cs_extraction_warning"),
        "is_integral": canonical in MAJOR_WETTED_PARTS,
        "is_structural": canonical in STRUCTURAL_PART_NAMES,
        "sap_expected": sap_expected,
        "discrepancies": [],
    }


def _presence_concern(ctx) -> bool:
    """True if an absence matters given document purpose (needs LLM judgment)."""
    in_cs, in_bom = ctx["cs"] is not None, ctx["bom"] is not None
    # CS ⊆ BOM: a CS part absent from BOM is always a concern
    if in_cs and not in_bom:
        return True
    # integral part must be in both CS and BOM
    if ctx["is_integral"] and (not in_cs or not in_bom):
        return True
    return False


def _stage1_clear(ctx) -> bool:
    """Clear deterministically only when fully consistent. Otherwise -> LLM."""
    if ctx.get("cs_extraction_warning"):
        return False                      # let the LLM adjudicate excluded CS material
    if _presence_concern(ctx):
        return False
    result = ctx["material_comparison"]["result"]
    if result == "MISMATCH":
        return False
    if result == "MATCH":
        if ctx["material_comparison"].get("coating_match") is False and not ctx["coating_required"]:
            return False
        return True
    # INSUFFICIENT (≤1 source has material) and no presence concern -> nothing to compare
    return True


# ═══════════════════════════════════════════════════════════════════════════
# Stage 2 + 3 — single GPT-5.5 call
# ═══════════════════════════════════════════════════════════════════════════

def _llm_stage23(parts: list, unresolved: list, sap_metadata: dict, sap_expected: set) -> list:
    payload = _build_llm_payload(parts, unresolved, sap_metadata)
    llm = _call_openai(payload)
    if llm is None:
        logger.error("LLM unavailable — conservative fallback flagging")
        _fallback_flag(parts)
        return []
    _apply_verdicts(parts, llm.get("verdicts", []))
    return llm.get("reconciliations", [])


def _build_llm_payload(parts: list, unresolved: list, sap_metadata: dict) -> dict:
    pump = sap_metadata.get("VT pump Common Name", "Unknown Pump")
    stages = sap_metadata.get("No of Stages", "?")
    part_rows = []
    for p in parts:
        row = {"part": p["canonical_name"],
               "is_integral": p["is_integral"], "sap_expected": p["sap_expected"],
               "rigid_result": p["material_comparison"]["result"],
               "families": p["material_comparison"].get("families", {})}
        for src in ("cs", "bom", "sap"):
            e = p.get(src)
            row[src] = None if not e else {"present": True, "material": e.get("material")}
        if p.get("cs_extraction_warning"):
            row["cs_note"] = p["cs_extraction_warning"]
        part_rows.append(row)
    return {"pump": pump, "stages": stages,
            "parts_to_evaluate": part_rows,
            "unresolved_names": unresolved}


def _call_openai(payload: dict):
    """Single rich GPT-5.5 call. Returns dict {verdicts, reconciliations} or None."""
    api_key = getattr(config, "OPENAI_API_KEY", None)
    if not api_key:
        logger.warning("OPENAI_API_KEY not set")
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        model = getattr(config, "OPENAI_MODEL", "gpt-4.1-mini")
        user_content = (
            LLM_INSTRUCTIONS
            + "\n\nPUMP: " + str(payload["pump"]) + "  STAGES: " + str(payload["stages"])
            + "\n\nPARTS TO EVALUATE (JSON):\n"
            + json.dumps(payload["parts_to_evaluate"], indent=1, ensure_ascii=False)
            + "\n\nUNRESOLVED NAMES (JSON):\n"
            + json.dumps(payload["unresolved_names"], indent=1, ensure_ascii=False)
        )
        kw = dict(model=model,
                  messages=[{"role": "system", "content": LLM_SYSTEM},
                            {"role": "user", "content": user_content}],
                  response_format={"type": "json_object"})
        resp = client.chat.completions.create(**kw)
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        logger.error(f"OpenAI comparison call failed: {e}")
        return None


def _apply_verdicts(parts: list, verdicts: list):
    by_name = {v.get("part", ""): v for v in verdicts}
    for p in parts:
        v = by_name.get(p["canonical_name"])
        if not v:
            logger.warning(f"No LLM verdict for '{p['canonical_name']}' — fallback")
            _fallback_flag_single(p)
            continue
        mc = p["material_comparison"]
        mc["method"] = "llm"
        mc["explanation"] = v.get("explanation", "")
        mc["authority"] = v.get("authority", "MANUAL_REVIEW")
        mc["correct_material"] = v.get("correct_material")
        discs = v.get("discrepancies", [])
        if v.get("status") == "FLAGGED" and discs:
            if any(d.get("type") == "MATERIAL_MISMATCH" for d in discs):
                mc["result"] = "MISMATCH"
            for d in discs:
                p["discrepancies"].append({
                    "type": d.get("type", "UNKNOWN"),
                    "source_in_error": d.get("source_in_error", "UNKNOWN"),
                    "reason": d.get("reason", "Flagged by AI evaluation"),
                    "detail": d.get("reason", ""),
                    "authority": v.get("authority", "MANUAL_REVIEW"),
                    "correct_material": v.get("correct_material"),
                })
        else:
            if mc["result"] == "MISMATCH":
                mc["result"] = "MATCH"
            p["discrepancies"] = [d for d in p["discrepancies"]
                                  if d.get("type") == "CS_EXTRACTION_WARNING"]


def _fallback_flag(parts: list):
    for p in parts:
        _fallback_flag_single(p)


def _fallback_flag_single(p: dict):
    mc = p["material_comparison"]
    mc["method"] = "fallback"
    mc["explanation"] = "AI evaluation unavailable — flagged for manual review"
    if mc["result"] == "MISMATCH":
        mats = {s: p[s]["material"] for s in ("cs", "bom", "sap")
                if p.get(s) and p[s].get("material")}
        p["discrepancies"].append({
            "type": "MATERIAL_MISMATCH", "source_in_error": "UNKNOWN",
            "reason": "Material conflict requires manual review: "
                      + ", ".join(f"{s}: {m}" for s, m in mats.items()),
            "detail": str(mats), "authority": "MANUAL_REVIEW", "correct_material": None,
        })
    elif _presence_concern(p):
        missing = [s for s in ("cs", "bom") if not p.get(s)]
        p["discrepancies"].append({
            "type": "MISSING", "source_in_error": ",".join(missing).upper() or "UNKNOWN",
            "reason": f"Part '{p['canonical_name']}' expected but absent from: "
                      + ", ".join(missing).upper(),
            "detail": "", "authority": "MANUAL_REVIEW", "correct_material": None,
        })


def _clean_for_output(part: dict) -> dict:
    discrepancies = list(part.get("discrepancies", []))
    if part.get("cs_extraction_warning"):
        if not any(d.get("type") == "CS_EXTRACTION_WARNING" for d in discrepancies):
            discrepancies.append({"type": "CS_EXTRACTION_WARNING",
                                  "reason": part["cs_extraction_warning"]})
    return {
        "canonical_name": part["canonical_name"],
        "cs": part["cs"], "bom": part["bom"], "sap": part["sap"],
        "material_comparison": part["material_comparison"],
        "discrepancies": discrepancies,
    }


# ═══════════════════════════════════════════════════════════════════════════
# BOM description helpers
# ═══════════════════════════════════════════════════════════════════════════

def _extract_bom_prefix(description: str):
    upper = description.upper()
    for prefix in _BOM_PART_PREFIXES:
        if upper.startswith(prefix):
            return prefix
    return None


def _extract_material_from_bom_desc(desc: str) -> tuple:
    upper = desc.upper().strip()
    has_coating = bool(re.search(r"\+\s*COAT", upper.replace(" ", "")))
    patterns = [
        r"(CA\d+\w*)(?:\+COAT)?$", r"(GGG\d+)(?:\+COAT)?$", r"(SS\s?\d{3}\w*)(?:\+CUTRUB)?$",
        r"CUT\s*RUB\w*\s+(SS\d+)$", r"(SS\d+)\+CUTRUB$", r"(FG\s?\d+)(?:\+COAT)?$",
        r"\b(HTS)$", r"\b(MS)$",
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


# ═══════════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════════

def _is_fastener_or_generic(desc: str) -> bool:
    upper = desc.upper()
    keywords = [
        "FASTNER", "FASTENER", "GASKET", "O' RING", "O RING", "'O' RING", "WASHER", "STUD",
        "HEX NUT", "HEX HD SCR", "SOC SET SCR", "SOC HD CAP", "HEX PLUG", "DOWEL PIN",
        "RIVET", "ERECTION PACKER", "FOUNDATION BOLT", "NAME PLATE", "INDICATOR ARROW",
        "CORD ", "BES KEY", "S-BER KEY", "KEY ", " KEY", "GLAND PACKING", "GLD PACK",
    ]
    return any(kw in upper for kw in keywords)


def _try_partial_resolve(desc: str, nom: Nomenclature):
    words = desc.split()
    for n in range(min(4, len(words)), 1, -1):
        r = nom.resolve(" ".join(words[:n]))
        if r:
            return r
    return None


def _dedupe_unresolved(items: list) -> list:
    seen, out = set(), []
    for u in items:
        key = (u.get("source", ""), u.get("original_name", "").upper().strip())
        if key not in seen:
            seen.add(key)
            out.append(u)
    return out


def _resolve_coating_requirement(sap_metadata: dict) -> bool:
    return sap_metadata.get("Coating Reqd By Customer", "").upper() in ("YES", "Y", "TRUE", "1")


def _load_json(path: Path, default=None):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


# ═══════════════════════════════════════════════════════════════════════════
# Validation  (unchanged — human confirms flags; disagreements learn aliases)
# ═══════════════════════════════════════════════════════════════════════════

def apply_validation(identifier: str, processed_dir: Path, decisions: list) -> dict:
    results = _load_json(processed_dir / "comparison_results.json", default={})
    nomenclature = Nomenclature()
    confirmed, dismissed, ignored, unresolved_items = [], [], [], []

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
            confirmed.append({"canonical_name": canonical,
                               "discrepancy_index": disc_index, "reason": reason})

        elif action == "disagree":
            mapped        = decision.get("mapped_canonical")
            original_name = decision.get("original_name")
            if mapped and original_name:
                nomenclature.add_alias(mapped, original_name)
            dismissed.append({"canonical_name": canonical,
                               "discrepancy_index": disc_index, "mapped_to": mapped})

        elif action == "ignore":
            ignored.append({"canonical_name": canonical, "discrepancy_index": disc_index})

        elif action == "unresolved":
            unresolved_items.append({"canonical_name": canonical})

    validation_status = {
        "identifier":               identifier,
        "timestamp":                datetime.now(timezone.utc).isoformat(),
        "confirmed_discrepancies":  confirmed,
        "dismissed_discrepancies":  dismissed,
        "ignored_discrepancies":    ignored,
        "unresolved_discrepancies": unresolved_items,
        "total_confirmed":          len(confirmed),
        "total_dismissed":          len(dismissed),
        "total_ignored":            len(ignored),
        "total_unresolved":         len(unresolved_items),
    }
    with open(processed_dir / "validation_status.json", "w") as f:
        json.dump(validation_status, f, indent=2)
    logger.info(
        f"Validation for {identifier}: {len(confirmed)} confirmed, "
        f"{len(dismissed)} dismissed, {len(ignored)} ignored, "
        f"{len(unresolved_items)} unresolved"
    )
    return validation_status