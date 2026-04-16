"""
Unified material handling for the BOM Components Validator.

Provides a single source of truth for:
  - Material code extraction from description strings (used by all extractors)
  - Rigid normalization for conservative Pass 1 comparison
  - Material matching logic for Pass 1 (string-based, zero false-positive risk)

All extractors and the comparator import from this module instead of
maintaining their own duplicate regex patterns.
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
    r"ASTM\s+A\d+[A-Z]?\s*,?\s*GR\.?\s*",   # ASTM A276 GR, ASTM A743, GR.
    r"A\d+\s+GR\s+",                          # A276 GR (without ASTM prefix)
    r"IS\s*:\s*\d+\s+GR[\s\-]*[A-Z]*\s*",    # IS:2062 GR-B, IS:2062 GR B
    r"IS\s+\d+\s+GR\s+",                      # IS 210 GR
    r"CI\s+IS\s+\d+\s+GR\s+",                # CI IS 210 GR (strip prefix, keep grade)
]


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
            # Append coating info if present in the original text
            if "+COAT" in upper.replace(" ", "") and "COAT" not in result.upper():
                result += " + COATING"
            return result
    return None


def normalize_for_rigid_comparison(raw_material: str) -> tuple[set[str], bool]:
    """Normalize a raw material string for rigid (Pass 1) comparison.

    Returns:
        (material_codes, has_coating)
        - material_codes: set of normalized material code strings
        - has_coating: whether coating was detected

    Conservative transformations only — no equivalence logic.
    This ensures zero false-positive risk in Pass 1.
    """
    if not raw_material:
        return set(), False

    upper = raw_material.upper().strip()

    # Detect and strip coating
    has_coating = bool(re.search(r"\+\s*COAT(?:ING)?", upper) or "COATING" in upper)
    upper = re.sub(r"\+?\s*COAT(?:ING)?", "", upper).strip()

    # Strip spec prefixes (try each pattern)
    for prefix_pat in SPEC_PREFIXES:
        upper = re.sub(prefix_pat, "", upper).strip()

    # Normalize dots in abbreviations: M.S. -> MS
    upper = re.sub(r"(\w)\.(\w)", r"\1\2", upper)   # M.S. -> MS.
    upper = re.sub(r"(\w)\.$", r"\1", upper)          # trailing dot: MS. -> MS

    # Strip trailing punctuation
    upper = upper.rstrip(".,;: ")

    # Handle "/" separator — split into multiple codes
    if "/" in upper:
        parts = [p.strip() for p in upper.split("/") if p.strip()]
        codes = set()
        for part in parts:
            # Each part after split might still need cleanup
            part = part.rstrip(".,;: ")
            if part:
                codes.add(part)
        return codes, has_coating

    # Handle parenthetical conditions like "SS410(T Condition)"
    paren_match = re.match(r"(\w+)\s*\(.*\)", upper)
    if paren_match:
        base = paren_match.group(1).strip()
        return {base}, has_coating

    # Single code
    if upper:
        return {upper}, has_coating

    return set(), has_coating


def rigid_materials_match(materials: dict[str, str | None], coatings: dict[str, bool]) -> dict:
    """Perform rigid (Pass 1) material comparison across sources.

    Args:
        materials: {source_name: raw_material_string} for each source that has the part
        coatings: {source_name: coating_bool} for each source

    Returns:
        dict with keys:
            "result": "MATCH" | "MISMATCH" | "INSUFFICIENT"
            "normalized": {source: set_of_codes} per source
            "coating_match": True/False/None
            "explanation": str (only for MATCH and INSUFFICIENT)
    """
    # Filter out sources with no material data
    available = {src: mat for src, mat in materials.items() if mat}

    if len(available) < 2:
        return {
            "result": "INSUFFICIENT",
            "normalized": {},
            "coating_match": None,
            "explanation": "Not enough material data across sources to compare",
        }

    # Normalize each source
    normalized = {}
    coating_flags = {}
    for src, raw in available.items():
        codes, mat_coating = normalize_for_rigid_comparison(raw)
        normalized[src] = codes
        # Use explicit coating field if available, else derive from material string
        coating_flags[src] = coatings.get(src, mat_coating)

    # Check if all pairs have overlapping codes
    sources = list(normalized.keys())
    all_match = True
    for i in range(len(sources)):
        for j in range(i + 1, len(sources)):
            codes_a = normalized[sources[i]]
            codes_b = normalized[sources[j]]
            if not codes_a or not codes_b:
                all_match = False
                break
            if not codes_a & codes_b:
                all_match = False
                break
        if not all_match:
            break

    # Check coating consistency
    coating_values = list(coating_flags.values())
    coating_match = len(set(coating_values)) <= 1 if coating_values else None

    # Build readable normalized strings for output
    normalized_readable = {
        src: ", ".join(sorted(codes)) if codes else ""
        for src, codes in normalized.items()
    }

    if all_match:
        # Build explanation
        common = set.intersection(*normalized.values()) if normalized.values() else set()
        common_str = ", ".join(sorted(common)) if common else "equivalent codes"
        return {
            "result": "MATCH",
            "normalized": normalized_readable,
            "coating_match": coating_match,
            "explanation": f"All sources specify {common_str} after normalization",
        }

    return {
        "result": "MISMATCH",
        "normalized": normalized_readable,
        "coating_match": coating_match,
        "explanation": None,  # Will be filled by Pass 2 (LLM) if needed
    }
