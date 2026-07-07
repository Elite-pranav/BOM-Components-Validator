"""
PDF report generator for the BOM Components Validator.

Report structure:
  Page 1
    - Title + identifier + timestamp
    - Pump metadata (compact table)
    - Discrepancy Alert section  — flagged parts only, with authority guidance
    - Extraction Warning section — CS_EXTRACTION_WARNING parts only (separate from errors)
    - Summary counts

  Page 2+
    - Full part-by-part comparison table (reference)
    - Confirmed discrepancies appendix
    - Dismissed (false positives) appendix

Key improvements over previous version:
  - Authority guidance: for each flagged part, the report states explicitly which
    document appears incorrect and what the correct material should be.
    e.g. "BOM appears incorrect — CS and SAP both say CI FG260."
  - For rigid MISSING flags (no LLM authority data), generates authority text
    programmatically from which sources are present/absent.
  - CS_EXTRACTION_WARNING is displayed separately from material errors so QA
    engineers understand it means "check your PDF extractor", not "wrong material".
  - STATUS column uses three distinct states: OK / FLAG / WARN
  - Discrepancy Alert is the headline — flagged items appear before the full table.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

# ── Colour palette ────────────────────────────────────────────────────────
_C_HEADER      = colors.HexColor("#1E293B")   # dark slate — table headers
_C_FLAG_BG     = colors.HexColor("#FEF2F2")   # light red   — flagged row bg
_C_FLAG_TEXT   = colors.HexColor("#DC2626")   # red         — flag status text
_C_WARN_BG     = colors.HexColor("#FFFBEB")   # light amber — warning row bg
_C_WARN_TEXT   = colors.HexColor("#D97706")   # amber       — warn status text
_C_OK_ALT      = colors.HexColor("#F8FAFC")   # off-white   — alternating rows
_C_ALERT_HDR   = colors.HexColor("#DC2626")   # red         — alert box header
_C_WARN_HDR    = colors.HexColor("#D97706")   # amber       — warning box header
_C_SUBTLE      = colors.HexColor("#64748B")   # slate grey  — subtitles
_C_BORDER      = colors.HexColor("#E2E8F0")   # light grey  — table grid


def generate_report(identifier: str, processed_dir: Path) -> Path:
    output_path = processed_dir / f"{identifier}_validation_report.pdf"

    comparison = _load_json(processed_dir / "comparison_results.json", {})
    validation = _load_json(processed_dir / "validation_status.json",  {})

    metadata = comparison.get("sap_metadata", {})
    parts    = comparison.get("parts", [])
    summary  = comparison.get("summary", {})

    confirmed_names = {
        d["canonical_name"]
        for d in validation.get("confirmed_discrepancies", [])
    }

    # Classify parts into three buckets upfront
    flagged_parts  = [p for p in parts if _has_real_discrepancy(p)]
    warning_parts  = [p for p in parts if _has_only_warnings(p)]
    ok_parts       = [p for p in parts if not p.get("discrepancies")]

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=20 * mm,  bottomMargin=20 * mm,
    )

    styles   = getSampleStyleSheet()
    elements = []

    # ── Title ─────────────────────────────────────────────────────────────
    elements += _build_title(identifier, styles)

    # ── Pump Metadata ──────────────────────────────────────────────────────
    if metadata:
        elements += _build_metadata_table(metadata, styles)

    # ── Discrepancy Alert (headline) ───────────────────────────────────────
    if flagged_parts:
        elements += _build_discrepancy_alert(
            flagged_parts, confirmed_names, parts, styles
        )
    else:
        elements += _build_all_clear_banner(styles)

    # ── Extraction Warnings ────────────────────────────────────────────────
    if warning_parts:
        elements += _build_extraction_warnings(warning_parts, styles)

    # ── Summary ────────────────────────────────────────────────────────────
    elements += _build_summary(
        summary, validation, len(flagged_parts), len(warning_parts), styles
    )

    elements.append(HRFlowable(width="100%", thickness=0.5,
                               color=_C_BORDER, spaceAfter=6 * mm))

    # ── Full Part Comparison Table ─────────────────────────────────────────
    if parts:
        elements += _build_comparison_table(parts, confirmed_names, styles)

    # ── Confirmed Discrepancies Appendix ──────────────────────────────────
    confirmed_list = validation.get("confirmed_discrepancies", [])
    if confirmed_list:
        elements += _build_confirmed_appendix(confirmed_list, parts, styles)

    # ── Dismissed Appendix ────────────────────────────────────────────────
    dismissed_list = validation.get("dismissed_discrepancies", [])
    if dismissed_list:
        elements += _build_dismissed_appendix(dismissed_list, styles)

    doc.build(elements)
    logger.info(f"Report generated: {output_path}")
    return output_path


# ── Section builders ──────────────────────────────────────────────────────

def _build_title(identifier: str, styles) -> list:
    title_style = ParagraphStyle(
        "ReportTitle", parent=styles["Title"],
        fontSize=18, spaceAfter=3 * mm,
        textColor=_C_HEADER,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle", parent=styles["Normal"],
        fontSize=9, textColor=_C_SUBTLE, spaceAfter=6 * mm,
    )
    return [
        Paragraph("BOM Validation Report", title_style),
        Paragraph(
            f"Identifier: <b>{identifier}</b> &nbsp;|&nbsp; "
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            subtitle_style,
        ),
    ]


# ── Metadata display configuration ──────────────────────────────────────────
# Priority keys: shown first if present, regardless of pump type.
# These are the most universally important fields across all pump families.
_META_PRIORITY_KEYS = [
    # Identity — always show these first
    "VT pump Common Name", "Pump Common Name", "Pump Type", "Pump Model",
    "No of Stages", "No of Stage", "Number of Stages",
    # Application context — critical for QA reviewer to know what they are signing off
    "Region", "Customer Tag Number", "Application", "Liquid Handled",
    "Manufacturing Clearance", "Scope of Supply",
    # Performance
    "Flow (m3/h)", "Nett Gen Head", "Bowl Head (m)", "Shut off Head (m)",
    "Bowl Efficiency (%)", "Pump BKW (kW)", "Motor Rating (kW)",
    # Configuration
    "Full Load Speed (RPM)", "Type of Sealing",
]

# Keys to always skip — internal/technical fields not useful for QA review.
_META_SKIP_KEYS = {
    # BOM/order admin
    "Pump Bill Of Material Status", "Pump BOM allocated", "sales document type",
    "E_VBAP_KWMENG", "evaluation compl.", "reference date of bill of mate",
    "Article Number 02", "Nameplate article", "Product Category",
    # Drawing numbers (long strings that overflow cells)
    "Order Against GA Drawing no", "Order Against CS Drawing no",
    "Motor_Eng_GearBox GADAttached?", "Appvd_Submited_GA_CS_Attached?",
    "Appvd_Spe_Coup_Drg_Attached?",
    # Dimensional details (better in engineering docs, not QA report)
    "Shaft Dia at Muff Coup (mm)", "Shaft Dia at Impeller",
    "Bell Mouth Bush OD", "Bell Mouth Bush Length", "Pump Shaft Extension",
    "VT Sleeve OD at Brg. Bkt", "VT Diffuser Sleeve OD",
    "VT Bellmouth Bearing Bush ID", "Provides Pump Suc Size",
    "Provides Pump Del Size",
    # Flags that are only meaningful in the configurator UI
    "Explosion is required", "CFD Required", "Base Frame Required In Tender",
    "Fumigation Required", "Fumigation Certificate Reqd?",
    "LD applicable for Document", "Painting Inspection",
    "Customer Template Available?",
}

# Maximum rows to show in the metadata table (keeps page 1 compact)
_META_MAX_ROWS = 16


def _select_metadata_keys(metadata: dict) -> list[str]:
    """
    Dynamically select which metadata keys to display in the report.

    Works for any pump type — no hardcoded key lists for specific models.

    Strategy:
      1. Start with priority keys that exist in this pump's metadata.
      2. Fill remaining slots with other non-skipped keys that have
         meaningful values, sorted alphabetically for consistency.
      3. Cap at _META_MAX_ROWS total.
    """
    selected = []

    # Pass 1: priority keys in defined order
    for key in _META_PRIORITY_KEYS:
        if key in metadata and _is_meaningful_value(metadata[key]):
            selected.append(key)
            if len(selected) >= _META_MAX_ROWS:
                return selected

    # Pass 2: remaining keys not in skip list and not already selected
    selected_set = set(selected)
    remaining = sorted(
        k for k in metadata
        if k not in selected_set
        and k not in _META_SKIP_KEYS
        and _is_meaningful_value(metadata[k])
    )
    for key in remaining:
        selected.append(key)
        if len(selected) >= _META_MAX_ROWS:
            break

    return selected


def _is_meaningful_value(value) -> bool:
    """Return True if a metadata value is worth displaying in the report."""
    if value is None:
        return False
    s = str(value).strip()
    if not s or s in ("-", "—", "N/A", "NOT APPLICABLE", "null", "None"):
        return False
    # Skip values that are purely numeric internal IDs (e.g. "4.000", "0.00")
    # but keep meaningful numeric values like flow rates and pressures
    # Heuristic: skip if value is a bare number with no units and looks like an ID
    import re as _re
    if _re.match(r"^[0-9]+[.][0]{3}$", s):   # e.g. "4.000" — SAP quantity field
        return False
    return True


def _build_metadata_table(metadata: dict, styles) -> list:
    """
    Build the pump metadata table dynamically from available SAP fields.
    Works for any pump type — key selection is driven by _select_metadata_keys().
    """
    selected_keys = _select_metadata_keys(metadata)

    rows = [["Field", "Value"]]
    for key in selected_keys:
        rows.append([key, str(metadata[key])])

    if len(rows) == 1:
        return []

    table = Table(rows, colWidths=[70 * mm, 100 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), _C_HEADER),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID",          (0, 0), (-1, -1), 0.5, _C_BORDER),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, _C_OK_ALT]),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
    ]))
    return [
        Paragraph("Pump Metadata", styles["Heading2"]),
        Spacer(1, 3 * mm),
        table,
        Spacer(1, 6 * mm),
    ]


def _build_all_clear_banner(styles) -> list:
    """Shown when there are zero flagged parts."""
    banner_style = ParagraphStyle(
        "AllClear", parent=styles["Normal"],
        fontSize=11, textColor=colors.HexColor("#166534"),
        backColor=colors.HexColor("#F0FDF4"),
        borderPad=8, leading=16,
        spaceBefore=2 * mm, spaceAfter=6 * mm,
    )
    return [
        Paragraph(
            "&#10003; &nbsp; <b>All Clear</b> — No material discrepancies found. "
            "All parts match across CS, BOM, and SAP.",
            banner_style,
        ),
        Spacer(1, 4 * mm),
    ]


def _build_discrepancy_alert(
    flagged_parts: list, confirmed_names: set, all_parts: list, styles
) -> list:
    """
    Headline section. One row per flagged part showing:
    - Part name
    - Which documents have conflicting values
    - Which document appears incorrect (authority guidance)
    - What the correct material should be
    """
    heading_style = ParagraphStyle(
        "AlertHeading", parent=styles["Heading2"],
        textColor=_C_ALERT_HDR, spaceAfter=3 * mm,
    )
    cell_style = ParagraphStyle(
        "AlertCell", parent=styles["Normal"],
        fontSize=8, leading=11,
    )
    bold_cell = ParagraphStyle(
        "AlertCellBold", parent=styles["Normal"],
        fontSize=8, leading=11, fontName="Helvetica-Bold",
    )

    rows = [[
        Paragraph("<b>Part Name</b>", bold_cell),
        Paragraph("<b>Conflict</b>", bold_cell),
        Paragraph("<b>Assessment</b>", bold_cell),
        Paragraph("<b>Confirmed</b>", bold_cell),
    ]]

    for part in flagged_parts:
        name         = part["canonical_name"]
        discs        = [d for d in part.get("discrepancies", [])
                        if d.get("type") != "CS_EXTRACTION_WARNING"]
        is_confirmed = name in confirmed_names

        # Build conflict string: what each source says
        conflict_lines = []
        for src in ("cs", "bom", "sap"):
            entry = part.get(src)
            mat   = _get_material(entry)
            if mat != "—":
                conflict_lines.append(f"<b>{src.upper()}:</b> {_shorten_material(mat)}")
            else:
                conflict_lines.append(f"<b>{src.upper()}:</b> absent")
        conflict_text = "<br/>".join(conflict_lines)

        # Build assessment using authority data
        assessment = _build_authority_text(part, discs)

        confirmed_text = "YES" if is_confirmed else "Pending"
        confirmed_color = _C_FLAG_TEXT if is_confirmed else _C_SUBTLE

        rows.append([
            Paragraph(name, bold_cell),
            Paragraph(conflict_text, cell_style),
            Paragraph(assessment, cell_style),
            Paragraph(
                f'<font color="{confirmed_color.hexval()}">{confirmed_text}</font>',
                cell_style
            ),
        ])

    col_widths = [42 * mm, 52 * mm, 62 * mm, 18 * mm]
    table = Table(rows, colWidths=col_widths, repeatRows=1)

    table_styles = [
        ("BACKGROUND",    (0, 0), (-1, 0), _C_ALERT_HDR),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("GRID",          (0, 0), (-1, -1), 0.5, _C_BORDER),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
    ]
    for i in range(1, len(rows)):
        bg = _C_FLAG_BG if i % 2 == 1 else colors.white
        table_styles.append(("BACKGROUND", (0, i), (-1, i), bg))

    table.setStyle(TableStyle(table_styles))

    n = len(flagged_parts)
    return [
        Paragraph(
            f"&#9888; &nbsp; Discrepancies Found: {n} part{'s' if n > 1 else ''} require attention",
            heading_style,
        ),
        Spacer(1, 2 * mm),
        table,
        Spacer(1, 6 * mm),
    ]


def _build_extraction_warnings(warning_parts: list, styles) -> list:
    """
    Separate section for CS_EXTRACTION_WARNING parts.
    These are NOT material errors — they indicate the PDF extractor
    assigned a consumable material to a structural part, which was
    then excluded from comparison.
    """
    heading_style = ParagraphStyle(
        "WarnHeading", parent=styles["Heading2"],
        textColor=_C_WARN_TEXT, spaceAfter=3 * mm,
    )
    note_style = ParagraphStyle(
        "WarnNote", parent=styles["Normal"],
        fontSize=8, textColor=_C_SUBTLE,
        spaceAfter=3 * mm, leading=11,
    )
    cell_style = ParagraphStyle(
        "WarnCell", parent=styles["Normal"],
        fontSize=8, leading=11,
    )

    rows = [["Part Name", "CS Material (excluded)", "Warning"]]
    for part in warning_parts:
        name = part["canonical_name"]
        warn = next(
            (d for d in part.get("discrepancies", [])
             if d.get("type") == "CS_EXTRACTION_WARNING"),
            {}
        )
        cs_mat  = _get_material(part.get("cs")) or "—"
        message = warn.get("reason", "CS material excluded — possible extraction error")
        # Shorten the message for the table
        short_msg = (
            "CS material looks like a consumable assigned to a structural part. "
            "Likely PDF row-span error. CS value excluded from comparison."
        )
        rows.append([name, _shorten_material(cs_mat), short_msg])

    table = Table(
        [[Paragraph(str(c), cell_style) for c in row] for row in rows],
        colWidths=[42 * mm, 40 * mm, 88 * mm],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), _C_WARN_HDR),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID",          (0, 0), (-1, -1), 0.5, _C_BORDER),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [_C_WARN_BG, colors.white]),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
    ]))

    n = len(warning_parts)
    return [
        Paragraph(
            f"&#9432; &nbsp; Extraction Warnings: {n} part{'s' if n > 1 else ''}",
            heading_style,
        ),
        Paragraph(
            "These are <b>not material errors</b>. The CS PDF extractor assigned a "
            "consumable material (e.g. graphited cotton, nitrile rubber) to a structural "
            "part — likely due to a merged/spanning row in the drawing. The CS value was "
            "excluded from comparison. Please verify the CS drawing manually and fix the "
            "extractor if needed.",
            note_style,
        ),
        table,
        Spacer(1, 6 * mm),
    ]


def _build_summary(
    summary: dict,
    validation: dict,
    n_flagged: int,
    n_warnings: int,
    styles,
) -> list:
    val_confirmed = validation.get("total_confirmed", 0)
    val_dismissed = validation.get("total_dismissed", 0)

    lines = [
        f"Total parts compared: <b>{summary.get('total_canonical_parts', 0)}</b>",
        f"Discrepancies flagged: <b>{n_flagged}</b>",
        f"Extraction warnings: <b>{n_warnings}</b>",
        f"Unresolved (not in nomenclature): <b>{summary.get('unresolved_parts', 0)}</b>",
        f"User confirmed errors: <b>{val_confirmed}</b>",
        f"User dismissed (false positives): <b>{val_dismissed}</b>",
    ]

    return [
        Paragraph("Summary", styles["Heading2"]),
        Spacer(1, 2 * mm),
        Paragraph(" &nbsp;&nbsp;|&nbsp;&nbsp; ".join(lines), styles["Normal"]),
        Spacer(1, 5 * mm),
    ]


def _build_comparison_table(parts: list, confirmed_names: set, styles) -> list:
    """Full 40-part reference table. Flags are highlighted but detail is in alert section."""
    cell_style = ParagraphStyle(
        "CompCell", parent=styles["Normal"],
        fontSize=7, leading=9,
    )

    header = ["Part Name", "CS", "BOM", "SAP", "Status"]
    rows   = [header]

    for part in parts:
        name    = part["canonical_name"]
        cs_mat  = _shorten_material(_get_material(part.get("cs")))
        bom_mat = _shorten_material(_get_material(part.get("bom")))
        sap_mat = _shorten_material(_get_material(part.get("sap")))

        discs        = part.get("discrepancies", [])
        real_discs   = [d for d in discs if d.get("type") != "CS_EXTRACTION_WARNING"]
        warn_discs   = [d for d in discs if d.get("type") == "CS_EXTRACTION_WARNING"]
        is_confirmed = name in confirmed_names

        if real_discs:
            status = "ERROR" if is_confirmed else "FLAG"
        elif warn_discs:
            status = "WARN"
        else:
            status = "OK"

        rows.append([name, cs_mat, bom_mat, sap_mat, status])

    col_widths = [45 * mm, 33 * mm, 33 * mm, 30 * mm, 14 * mm]
    table = Table(
        [[Paragraph(str(c), cell_style) for c in row] for row in rows],
        colWidths=col_widths,
        repeatRows=1,
    )

    table_styles = [
        ("BACKGROUND",    (0, 0), (-1, 0), _C_HEADER),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTSIZE",      (0, 0), (-1, -1), 7),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID",          (0, 0), (-1, -1), 0.5, _C_BORDER),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
    ]

    for i, row in enumerate(rows[1:], start=1):
        status = row[-1]
        if status in ("FLAG", "ERROR"):
            table_styles += [
                ("BACKGROUND", (0, i), (-1, i), _C_FLAG_BG),
                ("TEXTCOLOR",  (-1, i), (-1, i), _C_FLAG_TEXT),
                ("FONTNAME",   (-1, i), (-1, i), "Helvetica-Bold"),
            ]
        elif status == "WARN":
            table_styles += [
                ("BACKGROUND", (0, i), (-1, i), _C_WARN_BG),
                ("TEXTCOLOR",  (-1, i), (-1, i), _C_WARN_TEXT),
            ]
        else:
            bg = colors.white if i % 2 == 0 else _C_OK_ALT
            table_styles.append(("BACKGROUND", (0, i), (-1, i), bg))

    table.setStyle(TableStyle(table_styles))

    return [
        Paragraph("Full Part Comparison (Reference)", styles["Heading2"]),
        Spacer(1, 3 * mm),
        table,
        Spacer(1, 6 * mm),
    ]


def _build_confirmed_appendix(
    confirmed_list: list, parts: list, styles
) -> list:
    heading_style = ParagraphStyle(
        "AppendixHeading", parent=styles["Heading2"],
        textColor=_C_ALERT_HDR,
    )
    cell_style = ParagraphStyle(
        "AppCell", parent=styles["Normal"],
        fontSize=7, leading=10,
    )

    rows = [["Part Name", "Type", "Reason"]]
    for conf in confirmed_list:
        name      = conf["canonical_name"]
        disc_idx  = conf.get("discrepancy_index", 0)
        reason    = conf.get("reason", "")
        disc_type = ""

        part_data = next((p for p in parts if p["canonical_name"] == name), None)
        if part_data:
            discs = [d for d in part_data.get("discrepancies", [])
                     if d.get("type") != "CS_EXTRACTION_WARNING"]
            if disc_idx < len(discs):
                disc_type = discs[disc_idx].get("type", "")
                if not reason:
                    reason = discs[disc_idx].get("reason", "")
        if not reason and part_data:
            reason = part_data.get("material_comparison", {}).get("explanation", "")

        rows.append([name, disc_type, reason or "No reason provided"])

    table = Table(
        [[Paragraph(str(c), cell_style) for c in row] for row in rows],
        colWidths=[42 * mm, 28 * mm, 100 * mm],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), _C_ALERT_HDR),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTSIZE",      (0, 0), (-1, -1), 7),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID",          (0, 0), (-1, -1), 0.5, _C_BORDER),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [_C_FLAG_BG, colors.white]),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
    ]))

    return [
        Paragraph("Appendix A — Confirmed Discrepancies", heading_style),
        Spacer(1, 3 * mm),
        table,
        Spacer(1, 6 * mm),
    ]


def _build_dismissed_appendix(dismissed_list: list, styles) -> list:
    lines = []
    for d in dismissed_list:
        name   = d.get("canonical_name", "")
        mapped = d.get("mapped_to", "")
        lines.append(
            f"<b>{name}</b> — remapped to: {mapped}" if mapped
            else f"<b>{name}</b> — dismissed by user"
        )
    note_style = ParagraphStyle(
        "DismissNote", parent=styles["Normal"],
        fontSize=8, leading=12,
    )
    return [
        Paragraph("Appendix B — Dismissed (False Positives)", styles["Heading2"]),
        Spacer(1, 3 * mm),
        Paragraph("<br/>".join(lines), note_style),
        Spacer(1, 6 * mm),
    ]


# ── Authority text builder ─────────────────────────────────────────────────

def _build_authority_text(part: dict, discs: list) -> str:
    """
    Build a human-readable assessment string for the discrepancy alert table.

    Strategy (Option B):
      - If the LLM ran and returned authority/correct_material → use them directly.
      - If rigid pass found MISSING (no LLM) → generate programmatically.
      - If rigid pass found COATING_MISMATCH → generate programmatically.
      - Fallback → show a generic "manual review" message.
    """
    mat_comp       = part.get("material_comparison", {})
    authority      = mat_comp.get("authority")
    correct_mat    = mat_comp.get("correct_material")

    # ── Case 1: LLM provided authority data ───────────────────────────────
    if authority and authority not in ("MANUAL_REVIEW", None):
        doc_in_error = _source_in_error_from_discs(discs)
        correct_str  = f" Correct material: <b>{_shorten_material(correct_mat)}</b>." \
                       if correct_mat else ""
        if doc_in_error and doc_in_error != "UNKNOWN":
            return (
                f"<b>{doc_in_error}</b> appears incorrect — "
                f"{authority} {'is' if '+' not in authority else 'are'} the reference."
                f"{correct_str}"
            )
        return (
            f"Reference: <b>{authority}</b>.{correct_str}"
        )

    # ── Case 2: MISSING — rigid pass, no LLM ──────────────────────────────
    missing_disc = next((d for d in discs if d.get("type") == "MISSING"), None)
    if missing_disc:
        reason = missing_disc.get("reason", "")
        # Determine which sources are present
        present = [s.upper() for s in ("cs", "bom", "sap") if part.get(s)]
        absent  = [s.upper() for s in ("cs", "bom", "sap") if not part.get(s)]
        if absent:
            present_str = " and ".join(present) if present else "other documents"
            absent_str  = ", ".join(absent)
            return (
                f"Part absent from <b>{absent_str}</b>. "
                f"Present in {present_str} — verify procurement list."
            )
        return reason or "Part missing from one or more documents."

    # ── Case 3: COATING_MISMATCH only — rigid pass ────────────────────────
    coating_disc = next((d for d in discs if d.get("type") == "COATING_MISMATCH"), None)
    if coating_disc and not any(d.get("type") == "MATERIAL_MISMATCH" for d in discs):
        bom_has_coating = bool((part.get("bom") or {}).get("coating", False))
        cs_has_coating  = "COATING" in (_get_material(part.get("cs")) or "").upper()
        sap_has_coating = bool((part.get("sap") or {}).get("coating", False))
        if bom_has_coating and not cs_has_coating and not sap_has_coating:
            return (
                "BOM specifies coating; CS and SAP do not. "
                "Verify coating requirement — check SAP 'Coating Reqd By Customer' field."
            )
        return "Coating specification differs between documents. Verify manually."

    # ── Case 4: MATERIAL_MISMATCH from rigid, fallback (no LLM authority) ─
    mat_discs = [d for d in discs if d.get("type") == "MATERIAL_MISMATCH"]
    if mat_discs:
        # Try to infer authority from which sources agree
        cs_fam  = _get_family(part, "cs")
        bom_fam = _get_family(part, "bom")
        sap_fam = _get_family(part, "sap")

        if sap_fam and cs_fam and sap_fam == cs_fam and bom_fam != sap_fam:
            bom_mat = _shorten_material(_get_material(part.get("bom")))
            return (
                f"<b>BOM appears incorrect</b> — CS and SAP both agree. "
                f"BOM shows: {bom_mat}. Verify BOM entry."
            )
        if sap_fam and bom_fam and sap_fam == bom_fam and cs_fam != sap_fam:
            cs_mat = _shorten_material(_get_material(part.get("cs")))
            return (
                f"<b>CS appears incorrect</b> — SAP and BOM both agree. "
                f"CS shows: {cs_mat}. Verify CS drawing or extractor."
            )
        if cs_fam and bom_fam and cs_fam != bom_fam and not sap_fam:
            return (
                "CS and BOM disagree; SAP has no entry for this part. "
                "Manual review required."
            )

        reason = mat_discs[0].get("reason", "")
        return reason or "Material mismatch — manual review required."

    # ── Fallback ──────────────────────────────────────────────────────────
    if discs:
        return discs[0].get("reason", "Discrepancy flagged — manual review required.")
    return "Flagged — manual review required."


# ── Classification helpers ─────────────────────────────────────────────────

def _has_real_discrepancy(part: dict) -> bool:
    """True if part has any discrepancy that is NOT a CS extraction warning."""
    return any(
        d.get("type") != "CS_EXTRACTION_WARNING"
        for d in part.get("discrepancies", [])
    )


def _has_only_warnings(part: dict) -> bool:
    """True if part has ONLY CS extraction warnings (no real discrepancies)."""
    discs = part.get("discrepancies", [])
    return bool(discs) and all(
        d.get("type") == "CS_EXTRACTION_WARNING" for d in discs
    )


def _source_in_error_from_discs(discs: list) -> str | None:
    """Extract the source_in_error field from the first material mismatch."""
    for d in discs:
        src = d.get("source_in_error")
        if src and src != "UNKNOWN":
            return src
    return None


def _get_family(part: dict, source: str) -> str | None:
    """Get the resolved material family for a source from the comparison data."""
    families = part.get("material_comparison", {}).get("families", {})
    fam_str  = families.get(source, "")
    if not fam_str:
        return None
    # families are stored as comma-separated strings for multi-code entries
    # Take the first one for comparison purposes
    return fam_str.split(",")[0].strip() or None


# ── Material display helpers ───────────────────────────────────────────────

def _get_material(entry: dict | None) -> str:
    if not entry:
        return "—"
    mat = entry.get("material") or entry.get("raw_material") or ""
    return mat if mat else "—"


def _shorten_material(mat: str) -> str:
    """
    Shorten long material strings for table display.
    Strips common spec prefixes to keep cell content readable.
    Full strings are still available in the alert section via _get_material().
    """
    if not mat or mat == "—":
        return mat
    # Strip the most common verbose prefixes
    prefixes = [
        "ASTM A276 GR ", "ASTM A743 GR ", "ASTM A743, GR.",
        "A276 GR ", "CI IS 210 GR ", "IS:2062 GR-B ",
        "IS:2062, GR.E250 BR.", "M.S. IS:2062 GR-B",
        "M.S. IS:2062, GR.E250 BR.",
        # Full verbose forms that should show only the grade
        "M.S. IS:2062 GR-B", "M.S. IS:2062, GR.E250 BR.",
    ]
    # Add programmatic stripping of IS spec forms
    import re as _re
    result = mat.strip()
    # Strip M.S. IS:XXXX ... patterns first (whole string replacement)
    ms_match = _re.match(
        r"M\.S\.?\s+IS\s*:\s*\d+[^,]*(?:,\s*GR\.?[^)]*)?\s*", result, _re.IGNORECASE
    )
    if ms_match:
        result = "MS"
    else:
        for prefix in prefixes:
            if result.upper().startswith(prefix.upper()):
                result = result[len(prefix):].strip()
                break
    # Handle parenthetical condition: SS410(T Condition) → SS410
    import re
    result = re.sub(r"\(T Condition\)", "", result, flags=re.IGNORECASE).strip()
    # Cap length for table display
    if len(result) > 30:
        result = result[:28] + "…"
    return result


# ── JSON loader ────────────────────────────────────────────────────────────

def _load_json(path: Path, default=None):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default