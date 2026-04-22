"""
Unified material handling for the BOM Components Validator.

Provides a single source of truth for:
  - Material code extraction from description strings (used by all extractors)
  - Rigid normalization for conservative Pass 1 comparison
  - Semantic family equivalence for deterministic material matching
  - Material matching logic (string-based + family-aware, zero false-positive risk)

Design principle:
  Pass 1 (rigid) should clear ~90% of parts deterministically.
  Only genuine alloy family conflicts should reach Pass 2 (LLM).

Changes from previous version:
  - Added MATERIAL_FAMILY_MAP: maps normalized codes to canonical family names.
    This resolves equivalences like SS410T=SS410, CA15=SS410, M.S. IS:2062=MS.
  - Added _clean_fragments(): discards spec remnants (GR-B, E250, IS2062)
    that survive prefix stripping but are not material codes.
  - rigid_materials_match() now uses family comparison, not raw code intersection.
    Two sources match if they resolve to the same family, even if notations differ.
  - coating_required parameter added to rigid_materials_match() — if SAP metadata
    says coating is required, coating presence in BOM does not cause a mismatch.
"""

import re

# ── Shared material regex patterns ──────────────────────────────────────────
# Ordered by specificity: more specific patterns first to avoid partial matches.

MATERIAL_PATTERNS = [
    r"(CA\d+\w*)",                      # CA6NM, CA15
    r"(CF\d+\w*)",                      # CF8M, CF3M
    r"(SS\s?\d{3}\w*)",                 # SS304, SS410, SS410T, SS410H, SS 316L
    r"(GGG\s?\d+)",                     # GGG50
    r"(FG\s?\d+)",                      # FG260, FG 260
    r"(EN\s?\d+\w*)",                   # EN24
    r"(WCB)",                           # WCB
    r"(LTB\d+)",                        # LTB3
    r"\b(HTS)\b",                       # High Tensile Steel
    r"\b(CI)\b",                        # Cast Iron
    r"\b(M\.?S\.?)\b",                  # MS, M.S., M.S
    r"(CUT(?:LESS)?\s*RUBBER)",         # CUTLESS RUBBER
    r"(NITRILE(?:\s*RUBBER)?)",         # NITRILE, NITRILE RUBBER
    r"(COPPER)",                        # COPPER
    r"(CIP\s+Marine)",                  # CIP Marine
    r"(GRAPHITE\w*\s*COTTON\.?)",       # GRAPHITED COTTON
    r"(FORGED\s*STEEL\.?)",             # FORGED STEEL
]

# ── Spec prefixes to strip during rigid normalization ───────────────────────
# These are standard/specification labels, not material identifiers.
# Order: longest first to avoid partial matches.

SPEC_PREFIXES = [
    r"ASTM\s+A\d+[A-Z]?\s*,?\s*GR\.?\s*",        # ASTM A276 GR, ASTM A743, GR.
    r"A\d+\s+GR\s+",                               # A276 GR (without ASTM prefix)
    r"CI\s+IS\s+\d+\s+GR\s+",                     # CI IS 210 GR FG260 → FG260
    r"IS\s*:\s*\d+\s*,?\s*GR[\s\-]*[A-Z0-9]*\s*", # IS:2062 GR-B, IS:2062, GR.E250 BR.
    r"IS\s+\d+\s+GR\s+",                           # IS 210 GR
    r"\bGR[\s\-]*[A-Z0-9]+\b",                     # standalone GR-B, GR E250
]

# ── Spec fragment patterns — remnants to discard after prefix stripping ─────
# These survive SPEC_PREFIXES stripping but are not material codes.
# They cause false mismatches if left in the normalized code set.

_FRAGMENT_PATTERNS = [
    r"\bE\d{3}\b",          # E250, E350 — IS grade suffixes
    r"\bGR[\s\-]*[A-Z0-9]+\b",  # GR-B, GR B — grade labels
    r"\bIS\s*:\s*\d+\b",    # IS:2062 — standard number remnants
    r"\bIS\s+\d+\b",        # IS 210
    r"\bA\d{3}\b",          # A276, A743 — spec numbers without GR prefix
    r"\bBR\.?\b",           # BR. — suffix in M.S. IS:2062, GR.E250 BR.
    r"\bT\s+CONDITION\b",   # T CONDITION — heat treatment note in SAP
    r"\bCONDITION\b",       # CONDITION — standalone remnant
]

# ── Consumable material keywords ────────────────────────────────────────────
# Used to detect CS extraction errors: a structural part should never have
# a consumable material. If detected, the CS material is flagged as suspect
# and excluded from comparison to prevent false positives.

CONSUMABLE_KEYWORDS = [
    "COTTON", "RUBBER", "PACKING", "NITRILE", "GRAPHIT",
    "CORD", "O RING", "O-RING", "GASKET",
    "PTFE",         # polytetrafluoroethylene — gland packing material
    "TEFLON",       # brand name for PTFE
    "VITON",        # fluoroelastomer — sealing material
    "NEOPRENE",     # synthetic rubber — sealing material
    "ASBESTOS",     # legacy packing material (still appears in old drawings)
]

# ── Coating brand names ──────────────────────────────────────────────────────
# CS drawing extractors sometimes capture coating product names instead of
# base material codes. These brand names are NOT material grades — if one
# appears as the sole material for a structural part, it is a CS extraction
# error (the extractor picked up a coating annotation instead of the alloy).
# The cross-source check will then clear the false mismatch.

COATING_BRAND_KEYWORDS = [
    "CHAMPION AF",      # Champion AF 120 — Asian Paints epoxy coating
    "ASIAN GLASS",      # Asian glass flake epoxy
    "WILO GREEN",       # Wilo standard paint
    "QD ENAMEL",        # Quick-dry enamel paint
    "EPOXY",            # generic epoxy coating
    "GLASS FLAKE",      # glass flake epoxy
]

# ── Part type sets — built dynamically from nomenclature.json ───────────────
# These are populated at runtime by load_part_type_sets().
# Do NOT hardcode part names here — add the "type" field to nomenclature.json
# instead. This makes the system work for any pump type without code changes.
#
# Type values in nomenclature.json:
#   "wetted_structural" — major wetted parts: absence from BOM is flagged,
#                         consumable CS materials are treated as extraction errors
#   "structural"        — structural/mechanical parts: consumable CS materials
#                         are treated as extraction errors, BOM absence is OK
#   "consumable"        — packing, seals, O-rings: not compared for material
#   "accessory"         — bought-out items, instrumentation: not compared

# These are module-level sets populated by load_part_type_sets().
# They start empty and are filled on first call.
MAJOR_WETTED_PARTS: set[str]    = set()
STRUCTURAL_PART_NAMES: set[str] = set()

_part_types_loaded = False


def load_part_type_sets(nomenclature_path) -> None:
    """
    Populate MAJOR_WETTED_PARTS and STRUCTURAL_PART_NAMES from nomenclature.json.

    Called once at startup by the comparator. Safe to call multiple times.

    Args:
        nomenclature_path: Path to nomenclature.json
    """
    global MAJOR_WETTED_PARTS, STRUCTURAL_PART_NAMES, _part_types_loaded

    if _part_types_loaded:
        return

    import json
    from pathlib import Path

    path = Path(nomenclature_path)
    if not path.exists():
        import logging
        logging.getLogger(__name__).warning(
            f"nomenclature.json not found at {path} — "
            f"part type sets will be empty. Add 'type' fields to nomenclature.json."
        )
        _part_types_loaded = True
        return

    with open(path) as f:
        data = json.load(f)

    wetted   = set()
    structural = set()

    for canonical, info in data.items():
        part_type = info.get("type", "")
        if part_type == "wetted_structural":
            wetted.add(canonical)
            structural.add(canonical)   # wetted parts are also structural
        elif part_type == "structural":
            structural.add(canonical)
        # consumable and accessory: not added to either set

    MAJOR_WETTED_PARTS.update(wetted)
    STRUCTURAL_PART_NAMES.update(structural)
    _part_types_loaded = True

    import logging
    logging.getLogger(__name__).info(
        f"Loaded part types from nomenclature: "
        f"{len(wetted)} wetted_structural, "
        f"{len(structural - wetted)} structural-only"
    )

# ── Semantic family equivalence map ─────────────────────────────────────────
# Maps a normalized material code (uppercase, no spaces) to its canonical family.
#
# RULES applied here:
#   SS410T  = SS410   (T = tempered, same alloy)
#   SS410H  = SS410   (H = hardened, same alloy)
#   CA15    = SS410   (CA15 IS SS410 — martensitic stainless, ASTM A743 GR CA15)
#   CF8M    = SS316   (CF8M is the casting equivalent of SS316L)
#   CF3M    = SS316   (CF3M = low carbon variant)
#   FG260   = FG260   (cast iron grade — no equivalence to other families)
#   CI      = FG260   (bare "CI" in a context where FG260 is expected)
#   GGG50   = GGG50   (ductile/SG iron — distinct from grey cast iron)
#   MS      = MS      (mild steel — M.S., M.S. IS:2062, E250 all normalize here)
#   WCB     = MS      (ASTM A216 GR WCB = carbon steel casting, MS-equivalent)
#   HTS     = HTS     (high tensile steel — genuinely different from MS and SS)
#   CA6NM   = CA6NM   (13/4 martensitic SS — distinct from SS410)
#   FORGEDSTEEL = FORGEDSTEEL  (process description, not a specific grade)
#   COPPER  = COPPER
#   CUTLESSRUBBER = CUTLESSRUBBER  (bearing liner material)
#   NITRILERUBBER = NITRILERUBBER
#   GRAPHITEDCOTTON = GRAPHITEDCOTTON
#
# NOTE: FORGEDSTEEL is intentionally NOT mapped to MS or SS410 because
# "Forged Steel" in SAP for Muff Coupling is a known SAP data entry issue
# where the configurator uses a generic process term. The LLM handles this.

MATERIAL_FAMILY_MAP: dict[str, str] = {
    # SS410 family — all martensitic stainless variants
    "SS410":         "SS410",
    "SS410T":        "SS410",        # tempered
    "SS410H":        "SS410",        # hardened (short form)
    "SS410 HARDEN":  "SS410",        # hardened (SAP long form e.g. "SS410 HARDEN")
    "SS 410":        "SS410",
    "CA15":          "SS410",        # ASTM A743 GR CA15 = SS410 casting

    # SS304 family — austenitic
    "SS304":        "SS304",
    "SS 304":       "SS304",

    # SS316 family — austenitic + Mo
    "SS316":        "SS316",
    "SS316L":       "SS316",
    "CF8M":         "SS316",   # casting equivalent of SS316L
    "CF3M":         "SS316",   # low-carbon casting equivalent

    # CA6NM — 13/4 martensitic SS, distinct from SS410
    "CA6NM":        "CA6NM",

    # Cast iron families — keep separate, they are genuinely different
    "FG260":        "FG260",   # grey cast iron
    "CI":           "FG260",   # bare CI in pump context = FG260
    "FG 260":       "FG260",
    "GGG50":        "GGG50",   # ductile/SG iron — NOT same as FG260
    "GGG 50":       "GGG50",

    # Mild steel / carbon steel family
    "MS":           "MS",
    "M.S":          "MS",
    "M.S.":         "MS",
    "WCB":          "MS",      # ASTM A216 WCB = carbon steel casting

    # High tensile steel — genuinely different from plain MS
    "HTS":          "HTS",

    # NOTE: "FORGED STEEL" is intentionally absent from this map.
    # It is a process description, not a specific alloy grade, and its meaning
    # varies by pump type and document source. Unknown codes produce UNKNOWN:{code}
    # in rigid_materials_match() which triggers LLM evaluation automatically.
    # The LLM prompt handles Forged Steel in SAP couplings as a known generic entry.

    # Bearing / sealing materials
    "CUTLESSRUBBER":    "CUTLESS_RUBBER",
    "CUTLESS RUBBER":   "CUTLESS_RUBBER",
    "CUTLESSS RUBBER":  "CUTLESS_RUBBER",  # typo variant
    "NITRILE":          "NITRILE_RUBBER",
    "NITRILERUBBER":    "NITRILE_RUBBER",
    "NITRILE RUBBER":   "NITRILE_RUBBER",

    # Other materials
    "COPPER":           "COPPER",
    "GRAPHITEDCOTTON":  "GRAPHITED_COTTON",
    "GRAPHITED COTTON": "GRAPHITED_COTTON",
    "EN24":             "EN24",
}

# ── Composite material handling ──────────────────────────────────────────────
# Some materials combine a liner and a shell (e.g. CUTLESS RUBBER + SS410).
# For comparison purposes, the STRUCTURAL component (shell) determines the family.
# The liner is noted but does not cause a mismatch.
#
# Rule: if a material string contains both a liner keyword and a shell material,
# resolve to the shell material family.

_LINER_KEYWORDS = {"CUTLESS", "CUTLESS RUBBER", "RUBBER", "RUBBER LINED"}


def _resolve_composite(upper: str) -> str | None:
    """
    If the material string describes a composite (liner + shell),
    return the shell material code. Otherwise return None.

    Example:
      "CUTLESS RUBBER + SS410 SHELL" → "SS410"
      "CUTLESS + SS410"              → "SS410"
      "SS410 CUTL RUB"               → "SS410"
    """
    has_liner = any(kw in upper for kw in ("CUTLESS", "CUTL RUB", "CUT RUB"))
    if not has_liner:
        return None
    # Find the SS/CA/CF code in the composite string
    for pat in [r"(SS\s?\d{3}\w*)", r"(CA\d+\w*)", r"(CF\d+\w*)"]:
        m = re.search(pat, upper)
        if m:
            return m.group(1).replace(" ", "")
    return None


# ── Public API ───────────────────────────────────────────────────────────────

def extract_material_code(text: str) -> str | None:
    """Extract the first matching material code from a text string.

    Used by extractors to pull material info from description columns.
    Returns the matched code with optional coating suffix, or None.
    """
    if not text:
        return None
    upper = text.upper().strip()

    for pat in MATERIAL_PATTERNS:
        m = re.search(pat, upper)
        if m:
            result = m.group(1).strip()
            if "+COAT" in upper.replace(" ", "") and "COAT" not in result.upper():
                result += " + COATING"
            return result
    return None


def is_consumable_material(material: str) -> bool:
    """Return True if the material string looks like a pure consumable, not a structural material.

    Used to detect CS extraction errors where a consumable material
    (graphited cotton, nitrile rubber) was incorrectly assigned to a
    structural part due to PDF row spanning/merging during extraction.

    IMPORTANT: Composite bearing materials like "CUTLESS RUBBER+SS410 SHELL"
    or "SS410 CUTL RUB" are NOT consumables — they contain a structural alloy
    (SS410) as the shell material. This function returns False for composites
    so they are not incorrectly excluded from comparison.

    A string is consumable only if it contains a consumable keyword AND does
    NOT also contain a structural alloy code.
    """
    if not material:
        return False
    upper = material.upper()

    # Must contain at least one consumable keyword
    has_consumable = any(kw in upper for kw in CONSUMABLE_KEYWORDS)
    if not has_consumable:
        return False

    # If it also contains a structural alloy code, it is a composite — not a consumable
    # These patterns indicate a structural shell material is present
    # If the string also contains a structural alloy indicator, it is a composite.
    # Use simple substring checks — faster and no regex escape issues.
    _ALLOY_INDICATORS = (
        "SS4", "SS3",       # SS410, SS304, SS316 etc.
        "CA6NM", "CA15",    # martensitic stainless castings
        "CF8M", "CF3M",     # austenitic stainless castings
        "GGG50", "GGG 50",  # ductile iron
        "FG260", "FG 260",  # cast iron
        " HTS", "HTS ",     # high tensile steel (word-bounded)
    )
    if any(indicator in upper for indicator in _ALLOY_INDICATORS):
        return False  # Composite material — structural alloy present

    return True


def is_coating_brand(material: str) -> bool:
    """Return True if the material string looks like a coating brand name.

    CS drawing extractors sometimes capture coating product names (e.g.
    'CHAMPION AF 120') instead of base material codes. These are not
    material grades and should be treated as extraction errors when they
    appear as the sole material for a structural part.
    """
    if not material:
        return False
    upper = material.upper()
    return any(brand in upper for brand in COATING_BRAND_KEYWORDS)


def normalize_for_rigid_comparison(raw_material: str) -> tuple[set[str], bool]:
    """Normalize a raw material string to a set of cleaned material codes.

    Returns:
        (material_codes, has_coating)
        - material_codes: set of normalized material code strings (uppercase, no spaces)
        - has_coating: whether coating was detected

    This function handles syntactic cleanup only. For semantic equivalence
    (SS410T = SS410), use get_material_family() on each code.
    """
    if not raw_material:
        return set(), False

    upper = raw_material.upper().strip()

    # Detect and strip coating
    has_coating = bool(re.search(r"\+\s*COAT(?:ING)?", upper) or "COATING" in upper)
    upper = re.sub(r"\+?\s*COAT(?:ING)?", "", upper).strip()

    # Handle composite materials (liner + shell) before further stripping
    composite_shell = _resolve_composite(upper)
    if composite_shell:
        return {composite_shell}, has_coating

    # Strip spec prefixes (try each pattern, iterate to handle combinations)
    for _ in range(3):  # up to 3 passes to handle nested prefixes
        for prefix_pat in SPEC_PREFIXES:
            upper = re.sub(prefix_pat, "", upper, flags=re.IGNORECASE).strip()

    # Normalize dots in abbreviations: M.S. -> MS
    upper = re.sub(r"(\w)\.(\w)", r"\1\2", upper)   # M.S. -> MS
    upper = re.sub(r"(\w)\.$", r"\1", upper)          # trailing dot: MS. -> MS

    # Strip trailing punctuation
    upper = upper.rstrip(".,;: ")

    # Handle "/" separator — split into multiple codes
    if "/" in upper:
        raw_parts = [p.strip() for p in upper.split("/") if p.strip()]
        codes = set()
        for part in raw_parts:
            part = _clean_fragments(part.rstrip(".,;: "))
            if part:
                codes.add(part)
        return codes, has_coating

    # Handle parenthetical conditions like "SS410(T Condition)"
    paren_match = re.match(r"(\w+)\s*\(.*\)", upper)
    if paren_match:
        base = paren_match.group(1).strip()
        base = _clean_fragments(base)
        return {base} if base else set(), has_coating

    # Clean remaining spec fragments
    upper = _clean_fragments(upper)

    if upper:
        return {upper}, has_coating

    return set(), has_coating


def get_material_family(code: str) -> str | None:
    """Map a normalized material code to its canonical family name.

    Returns the family string if found, or None if unknown.

    Examples:
        "SS410T"  → "SS410"
        "CA15"    → "SS410"
        "MS"      → "MS"
        "FG260"   → "FG260"
        "UNKNOWN" → None
    """
    if not code:
        return None
    # Try exact match first
    clean = code.strip().upper()
    if clean in MATERIAL_FAMILY_MAP:
        return MATERIAL_FAMILY_MAP[clean]
    # Try without spaces
    nospace = clean.replace(" ", "")
    if nospace in MATERIAL_FAMILY_MAP:
        return MATERIAL_FAMILY_MAP[nospace]
    return None


def rigid_materials_match(
    materials: dict[str, str | None],
    coatings: dict[str, bool],
    coating_required: bool = False,
) -> dict:
    """Perform rigid (Pass 1) material comparison across sources.

    Uses semantic family equivalence — SS410T and SS410 are the same family
    and will MATCH. This eliminates false mismatches from notation differences.

    Args:
        materials: {source_name: raw_material_string} for each source that has the part
        coatings: {source_name: coating_bool} for each source
        coating_required: if True (from SAP metadata "Coating Reqd By Customer: YES"),
                          coating presence in BOM is expected and never flagged.

    Returns:
        dict with keys:
            "result": "MATCH" | "MISMATCH" | "INSUFFICIENT"
            "normalized": {source: normalized_code_string} per source
            "families": {source: family_string} per source
            "coating_match": True/False/None
            "explanation": str
    """
    # Filter out sources with no material data
    available = {src: mat for src, mat in materials.items() if mat}

    if len(available) < 2:
        return {
            "result": "INSUFFICIENT",
            "normalized": {},
            "families": {},
            "coating_match": None,
            "explanation": "Not enough material data across sources to compare",
        }

    # Normalize each source and resolve to families
    normalized = {}   # {src: set of raw codes}
    families   = {}   # {src: set of family strings}
    coating_flags = {}

    for src, raw in available.items():
        codes, mat_coating = normalize_for_rigid_comparison(raw)
        normalized[src] = codes

        # Resolve each code to a family
        # For dual-spec materials like "CF8M/SS410H" (meaning CF8M OR SS410H),
        # both families are added to the set. A source matches another if ANY
        # of its families intersect — the slash means "either is acceptable".
        src_families = set()
        unknown_codes = []
        for code in codes:
            family = get_material_family(code)
            if family:
                src_families.add(family)
            else:
                unknown_codes.append(code)

        if unknown_codes and not src_families:
            # All codes unknown — keep as UNKNOWN so it only matches itself
            for code in unknown_codes:
                src_families.add(f"UNKNOWN:{code}")
        # If src_families already has known families, unknown codes are dropped:
        # a dual-spec like "CF8M/SS410H" where CF8M is known resolves cleanly
        # to {SS316, SS410} without polluting the set with UNKNOWN entries.
        # This prevents unknown SAP notation variants from causing false mismatches.
        elif unknown_codes and src_families:
            pass  # known families take precedence — drop unknowns silently

        families[src] = src_families

        # Use explicit coating field if available, else derive from material string
        coating_flags[src] = coatings.get(src, mat_coating)

    # ── Material family match check ──────────────────────────────────────
    # All pairs must share at least one common family
    sources = list(families.keys())
    all_match = True
    for i in range(len(sources)):
        for j in range(i + 1, len(sources)):
            fam_a = families[sources[i]]
            fam_b = families[sources[j]]
            if not fam_a or not fam_b:
                all_match = False
                break
            if not fam_a & fam_b:
                all_match = False
                break
        if not all_match:
            break

    # ── Coating match check ──────────────────────────────────────────────
    if coating_required:
        # SAP metadata says coating is required — BOM having coating is correct.
        # Never flag coating as a mismatch in this case.
        coating_match = True
    else:
        coating_values = list(coating_flags.values())
        coating_match = len(set(coating_values)) <= 1 if coating_values else None

    # ── Build readable output ────────────────────────────────────────────
    normalized_readable = {
        src: ", ".join(sorted(codes)) if codes else ""
        for src, codes in normalized.items()
    }
    families_readable = {
        src: ", ".join(sorted(fams)) if fams else ""
        for src, fams in families.items()
    }

    if all_match:
        # Find the common family for explanation
        all_family_sets = list(families.values())
        common = set.intersection(*all_family_sets) if all_family_sets else set()
        common_str = ", ".join(sorted(common)) if common else "equivalent materials"
        return {
            "result":      "MATCH",
            "normalized":  normalized_readable,
            "families":    families_readable,
            "coating_match": coating_match,
            "explanation": f"All sources resolve to family: {common_str}",
        }

    # Build mismatch explanation showing the family conflict clearly
    family_summary = "; ".join(
        f"{src}→{fam}" for src, fam in families_readable.items()
    )
    return {
        "result":      "MISMATCH",
        "normalized":  normalized_readable,
        "families":    families_readable,
        "coating_match": coating_match,
        "explanation": f"Family conflict: {family_summary}",
    }


# ── Fragment cleanup ─────────────────────────────────────────────────────────

def _clean_fragments(code: str) -> str:
    """Remove spec remnants that survive prefix stripping but are not material codes.

    Examples of fragments removed:
      "GR-B"    (IS grade suffix)
      "E250"    (IS grade number)
      "IS:2062" (standard number)
      "BR."     (suffix in M.S. IS:2062, GR.E250 BR.)
      "T CONDITION" (SAP heat treatment note)
    """
    if not code:
        return code
    result = code.upper().strip()
    for frag_pat in _FRAGMENT_PATTERNS:
        result = re.sub(frag_pat, "", result, flags=re.IGNORECASE).strip()
    result = result.strip(".,;:- ")
    return result