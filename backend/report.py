"""
PDF report generator for the BOM Components Validator.

Generates a validation report containing:
  - Pump metadata (from SAP data)
  - Comparison summary statistics
  - Part-by-part comparison table with material and status
  - Confirmed discrepancies with per-mismatch reasons/explanations
  - Dismissed (false positive) summary

Uses reportlab for PDF generation.
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
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)


def generate_report(identifier: str, processed_dir: Path) -> Path:
    """Generate a PDF validation report and return the output path."""
    output_path = processed_dir / f"{identifier}_validation_report.pdf"

    comparison = _load_json(processed_dir / "comparison_results.json", {})
    validation = _load_json(processed_dir / "validation_status.json", {})
    sap_data = _load_json(processed_dir / "sap_data.json", {})

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    elements = []

    # ── Title ────────────────────────────────────────────────────────────
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontSize=18,
        spaceAfter=4 * mm,
        textColor=colors.HexColor("#1E293B"),
    )
    elements.append(Paragraph("BOM Validation Report", title_style))

    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#64748B"),
        spaceAfter=8 * mm,
    )
    elements.append(
        Paragraph(
            f"Identifier: {identifier} &nbsp;|&nbsp; "
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            subtitle_style,
        )
    )

    # ── Pump Metadata ────────────────────────────────────────────────────
    metadata = sap_data.get("metadata", {})
    if metadata:
        elements.append(Paragraph("Pump Metadata", styles["Heading2"]))
        elements.append(Spacer(1, 3 * mm))

        meta_keys = [
            "VT pump Common Name", "No of Stages", "Flow (m3/h)",
            "Shut off Head (m)", "Motor Rating (kW)", "Region",
            "Manufacturing Clearance", "Liquid Handled",
            "Full Load Speed (RPM)", "Coupling Type",
            "Type of Sealing", "Scope of Supply",
        ]
        meta_rows = [["Field", "Value"]]
        for key in meta_keys:
            if key in metadata:
                meta_rows.append([key, str(metadata[key])])

        if len(meta_rows) > 1:
            meta_table = Table(meta_rows, colWidths=[70 * mm, 100 * mm])
            meta_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ]))
            elements.append(meta_table)
            elements.append(Spacer(1, 6 * mm))

    # ── Summary ──────────────────────────────────────────────────────────
    summary = comparison.get("summary", {})
    val_confirmed = validation.get("total_confirmed", 0)
    val_dismissed = validation.get("total_dismissed", 0)

    elements.append(Paragraph("Comparison Summary", styles["Heading2"]))
    elements.append(Spacer(1, 3 * mm))

    summary_text = (
        f"Total canonical parts compared: <b>{summary.get('total_canonical_parts', 0)}</b><br/>"
        f"Discrepancies found: <b>{summary.get('discrepancies_found', 0)}</b><br/>"
        f"Unresolved parts: <b>{summary.get('unresolved_parts', 0)}</b><br/>"
        f"User confirmed errors: <b>{val_confirmed}</b><br/>"
        f"User dismissed (false positives): <b>{val_dismissed}</b>"
    )
    elements.append(Paragraph(summary_text, styles["Normal"]))
    elements.append(Spacer(1, 6 * mm))

    # ── Part Comparison Table ────────────────────────────────────────────
    parts = comparison.get("parts", [])
    if parts:
        elements.append(Paragraph("Part-by-Part Comparison", styles["Heading2"]))
        elements.append(Spacer(1, 3 * mm))

        header = ["Part Name", "CS", "BOM", "SAP", "Status"]
        table_data = [header]

        # Confirmed canonical names for highlighting
        confirmed_names = {
            d["canonical_name"]
            for d in validation.get("confirmed_discrepancies", [])
        }

        for part in parts:
            name = part["canonical_name"]
            cs_mat = _get_material(part.get("cs"))
            bom_mat = _get_material(part.get("bom"))
            sap_mat = _get_material(part.get("sap"))

            if part["discrepancies"]:
                if name in confirmed_names:
                    status = "ERROR"
                else:
                    status = "FLAGGED"
            else:
                status = "OK"

            table_data.append([name, cs_mat, bom_mat, sap_mat, status])

        col_widths = [45 * mm, 35 * mm, 35 * mm, 30 * mm, 20 * mm]
        comp_table = Table(table_data, colWidths=col_widths, repeatRows=1)

        table_styles = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ]

        for i, row in enumerate(table_data[1:], start=1):
            status = row[-1]
            if status == "ERROR":
                table_styles.append(
                    ("BACKGROUND", (0, i), (-1, i), colors.HexColor("#FEF2F2"))
                )
                table_styles.append(
                    ("TEXTCOLOR", (-1, i), (-1, i), colors.HexColor("#DC2626"))
                )
            elif status == "FLAGGED":
                table_styles.append(
                    ("BACKGROUND", (0, i), (-1, i), colors.HexColor("#FFFBEB"))
                )
            else:
                bg = colors.white if i % 2 == 0 else colors.HexColor("#F8FAFC")
                table_styles.append(("BACKGROUND", (0, i), (-1, i), bg))

        comp_table.setStyle(TableStyle(table_styles))
        elements.append(comp_table)
        elements.append(Spacer(1, 6 * mm))

    # ── Confirmed Discrepancies Detail (with reasons) ───────────────────
    confirmed_list = validation.get("confirmed_discrepancies", [])
    if confirmed_list:
        elements.append(Paragraph("Confirmed Discrepancies", styles["Heading2"]))
        elements.append(Spacer(1, 3 * mm))

        disc_header = ["Part Name", "Type", "Reason"]
        disc_table_data = [disc_header]

        for conf in confirmed_list:
            name = conf["canonical_name"]
            disc_idx = conf.get("discrepancy_index", 0)
            reason = conf.get("reason", "")

            # Find the part in comparison results for type info
            part_data = next(
                (p for p in parts if p["canonical_name"] == name), None
            )
            disc_type = ""
            if part_data:
                discs = part_data.get("discrepancies", [])
                if disc_idx < len(discs):
                    disc_type = discs[disc_idx].get("type", "")
                    if not reason:
                        reason = discs[disc_idx].get("reason", discs[disc_idx].get("detail", ""))

            # Also check material_comparison for LLM explanation
            if not reason and part_data:
                mat_comp = part_data.get("material_comparison", {})
                reason = mat_comp.get("explanation", "")

            disc_table_data.append([name, disc_type, reason or "No reason provided"])

        if len(disc_table_data) > 1:
            disc_col_widths = [40 * mm, 30 * mm, 100 * mm]
            disc_table = Table(disc_table_data, colWidths=disc_col_widths, repeatRows=1)
            disc_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DC2626")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#FEF2F2"), colors.white]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ]))
            elements.append(disc_table)
            elements.append(Spacer(1, 6 * mm))

    # ── Dismissed (false positives) summary ─────────────────────────────
    dismissed_list = validation.get("dismissed_discrepancies", [])
    if dismissed_list:
        elements.append(Paragraph("Dismissed (False Positives)", styles["Heading2"]))
        elements.append(Spacer(1, 3 * mm))

        dismissed_text_parts = []
        for d in dismissed_list:
            name = d.get("canonical_name", "")
            mapped = d.get("mapped_to", "")
            if mapped:
                dismissed_text_parts.append(f"<b>{name}</b> — remapped to: {mapped}")
            else:
                dismissed_text_parts.append(f"<b>{name}</b> — dismissed by user")

        dismissed_text = "<br/>".join(dismissed_text_parts)
        elements.append(Paragraph(dismissed_text, styles["Normal"]))
        elements.append(Spacer(1, 6 * mm))

    # Build PDF
    doc.build(elements)
    logger.info(f"Report generated: {output_path}")
    return output_path


def _load_json(path: Path, default=None):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


def _get_material(entry: dict | None) -> str:
    if not entry:
        return "\u2014"
    mat = entry.get("material") or entry.get("raw_material") or ""
    return mat if mat else "\u2014"
