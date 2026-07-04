"""
Cross-Section (CS) PDF extractor — deterministic geometry engine.

Design: deterministic-primary, OpenAI-fallback-only.
  Stage 0  Orientation normalisation  — rotate word/char coordinates so the
           table header row is always horizontal.
  Stage 1  Table localisation         — LLM reads the compact word-layout text
           to find the header row position, column roles, and bbox. Output is
           validated against real geometry before it is trusted; if validation
           fails (or the API is down) a deterministic synonym-based fallback runs.
  Stage 2  Geometry cell extraction   — column boundaries are computed from
           DATA whitespace valleys (not header positions); rows are anchored on
           reference numbers. Word text is reconstructed from glyph positions to
           repair AutoCAD character-spacing artefacts.
  Stage 3  Confidence flags           — per-row flags (empty ref/description,
           non-clean qty) are stripped before saving; the pipeline fails loud
           if both paths return nothing.

Output: cs_bom.json — a JSON array of
  {"ref": "...", "description": "...", "qty": "...", "material": "..."}
"""

import json
import logging
import re
import statistics
from pathlib import Path

import pdfplumber

from backend import config
from backend.extractors.base import BaseExtractor

logger = logging.getLogger(__name__)

# ── Header label synonyms ────────────────────────────────────────────────────
HEADER_SYNONYMS = {
    "ref":         re.compile(r"^(REF|ITEM|PART|SL|S\.?NO|NO)\.?$", re.I),
    "description": re.compile(r"^(DESC|DESCRIPTION|PARTICULARS?)\.?$", re.I),
    "qty":         re.compile(r"^(QTY|QUANTITY|NOS?|NO)\.?$", re.I),
    "material":    re.compile(r"^(MATL?|MATERIAL|MOC)\.?$", re.I),
}
_DEFAULT_REF_RE = r"\d{2,6}([-/]\d+)?"

# ── LLM discovery schema + system prompt ─────────────────────────────────────
_DISCOVERY_SCHEMA = {
    "name": "table_config",
    "schema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "table_found":      {"type": "boolean"},
            "header_top":       {"type": "number"},
            "data_direction":   {"type": "string", "enum": ["above", "below"]},
            "table_bbox": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "x_min": {"type": "number"}, "x_max": {"type": "number"},
                    "y_min": {"type": "number"}, "y_max": {"type": "number"},
                },
                "required": ["x_min", "x_max", "y_min", "y_max"],
            },
            "columns": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "role":     {"type": "string",
                                     "enum": ["ref", "description", "qty", "material", "ignore"]},
                        "label":    {"type": "string"},
                        "header_x": {"type": "number"},
                    },
                    "required": ["role", "label", "header_x"],
                },
            },
            "ref_format": {"type": "string"},
            "notes":       {"type": "string"},
        },
        "required": ["table_found", "header_top", "data_direction",
                     "table_bbox", "columns", "ref_format", "notes"],
    },
}

_DISCOVERY_SYSTEM = (
    "You read AutoCAD engineering-drawing layouts given as 'y{top}: x{x0}:word ...'.\n"
    "Locate the PARTS LIST / BILL OF MATERIALS table and return ONLY its metadata.\n"
    "Do NOT return any part names, quantities, or materials — metadata only.\n"
    "- table_bbox must be tight around the table only, excluding title block, notes, "
    "dimensions, drawing.\n"
    "- header_top is the y of the header row; data_direction = whether data rows are "
    "'above' (smaller y) or 'below'.\n"
    "- For each column: role (ref/description/qty/material, or 'ignore' for extras like "
    "REMARKS/WEIGHT/ZONE), its label text, and header_x (x0 of the header word).\n"
    "- ref_format: a Python regex matching the reference tokens you see."
)


class CSExtractor(BaseExtractor):
    """Extracts BOM from Cross-Section (CS) PDF drawings using geometry + optional OpenAI."""

    def extract(self) -> list:
        cs_pdf = self._find_cs_pdf()
        if not cs_pdf:
            logger.error(f"No CS PDF found in {self.raw_folder}")
            return []

        try:
            rows = self._run_pipeline(cs_pdf)
        except Exception as e:
            logger.error(f"CS extraction failed: {e}")
            return []

        clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
        self._save_json(clean, self.processed_folder / "cs_bom.json")
        logger.info(f"Extracted {len(clean)} parts from CS drawing")
        return clean

    # ── File discovery ────────────────────────────────────────────────────────

    def _find_cs_pdf(self) -> Path | None:
        # Match any PDF whose stem contains " CS" or ends with "CS"
        # e.g. "81355130-10 CS.pdf", "81360161-30 CS (1).pdf"
        for f in self.raw_folder.iterdir():
            if f.suffix.lower() == ".pdf" and " CS" in f.stem.upper():
                return f
        return None

    # ── Pipeline ──────────────────────────────────────────────────────────────

    def _run_pipeline(self, pdf_path: Path) -> list:
        client = self._make_openai_client()
        results = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for pi, page in enumerate(pdf.pages):
                words, chars, rotation = _normalise_orientation(page)
                if not _header_findable(words):
                    logger.debug(f"  page {pi}: no parts table found, skipped")
                    continue
                cfg = _build_config(words, client, config.OPENAI_MODEL)
                rows = _extract_rows(chars, cfg)
                if rows:
                    results.extend(_validate_rows(rows))
                    logger.info(
                        f"  page {pi}: {len(rows)} rows "
                        f"(config: {cfg['_source']}, rot={rotation})"
                    )
        return results

    def _make_openai_client(self):
        if not config.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY not set — CS extraction runs deterministically only")
            return None
        try:
            from openai import OpenAI
            return OpenAI(api_key=config.OPENAI_API_KEY)
        except Exception as e:
            logger.warning(f"Could not create OpenAI client: {e}")
            return None


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 0 — Orientation normalisation
# ═══════════════════════════════════════════════════════════════════════════════

def _load_page_words(page):
    return page.extract_words(x_tolerance=3, y_tolerance=3), page.chars


def _rotate_coords(items, k, W, H):
    out = []
    for it in items:
        x0, x1, t, b = it["x0"], it["x1"], it["top"], it["bottom"]
        if k % 4 == 0:
            nx0, nx1, nt, nb = x0, x1, t, b
        elif k % 4 == 1:
            nx0, nx1, nt, nb = H - b, H - t, x0, x1
        elif k % 4 == 2:
            nx0, nx1, nt, nb = W - x1, W - x0, H - b, H - t
        else:
            nx0, nx1, nt, nb = t, b, W - x1, W - x0
        d = dict(it)
        d.update(x0=nx0, x1=nx1, top=nt, bottom=nb)
        out.append(d)
    return out


def _header_findable(words) -> bool:
    rows = {}
    for w in words:
        for role, pat in HEADER_SYNONYMS.items():
            if pat.match(w["text"].strip()):
                rows.setdefault(round(w["top"]), set()).add(role)
    return max((len(v) for v in rows.values()), default=0) >= 2


def _normalise_orientation(page):
    W, H = page.width, page.height
    words, chars = _load_page_words(page)
    for k in (0, 1, 2, 3):
        rw = _rotate_coords(words, k, W, H)
        if _header_findable(rw):
            return rw, _rotate_coords(chars, k, W, H), k
    return words, chars, 0


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 1 — Table localisation (LLM + deterministic fallback)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_layout_text(words) -> str:
    bands = {}
    for w in words:
        bands.setdefault(round(w["top"] / 4) * 4, []).append(w)
    lines = []
    for top in sorted(bands):
        row = sorted(bands[top], key=lambda w: w["x0"])
        lines.append(
            f"y{top}: " + " ".join(f"x{round(w['x0'])}:{w['text']}" for w in row)
        )
    return "\n".join(lines)


def _llm_discover(client, model: str, layout_text: str):
    if client is None:
        return None
    try:
        kw = dict(
            model=model,
            messages=[
                {"role": "system", "content": _DISCOVERY_SYSTEM},
                {"role": "user",   "content": layout_text},
            ],
            response_format={"type": "json_schema", "json_schema": _DISCOVERY_SCHEMA},
        )
        resp = client.chat.completions.create(**kw)
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        logger.warning(f"  [Stage1] LLM discovery failed ({e}); falling back.")
        return None


def _detect_header_fallback(words) -> dict:
    cand = {}
    for w in words:
        for role, pat in HEADER_SYNONYMS.items():
            if pat.match(w["text"].strip()):
                cand.setdefault(round(w["top"]), {}).setdefault(role, w)
    if not cand:
        raise ValueError("No header row found by fallback")
    best = max(cand, key=lambda k: len(cand[k]))
    hits = cand[best]
    cols = sorted(
        [{"role": r, "label": w["text"], "header_x": w["x0"]} for r, w in hits.items()],
        key=lambda c: c["header_x"],
    )
    top = statistics.median(w["top"] for w in hits.values())
    return {
        "table_found": True,
        "header_top": top,
        "data_direction": "above",
        "table_bbox": {
            "x_min": min(c["header_x"] for c in cols) - 6,
            "x_max": 1e9, "y_min": 0, "y_max": top,
        },
        "columns": cols,
        "ref_format": _DEFAULT_REF_RE,
        "notes": "fallback",
    }


def _validate_config(cfg, words) -> tuple[bool, str]:
    try:
        if not cfg.get("table_found"):
            return False, "table_found false"
        cols = [c for c in cfg["columns"] if c["role"] != "ignore"]
        roles = {c["role"] for c in cols}
        if "ref" not in roles or "description" not in roles:
            return False, "missing ref/description"
        xs = [c["header_x"] for c in sorted(cols, key=lambda c: c["header_x"])]
        if any(xs[i] >= xs[i + 1] for i in range(len(xs) - 1)):
            return False, "columns not monotonic"
        for c in cols:
            pat = HEADER_SYNONYMS.get(c["role"])
            near = [
                w for w in words
                if abs(w["x0"] - c["header_x"]) <= 8
                and abs(w["top"] - cfg["header_top"]) <= 8
            ]
            if pat and not any(pat.match(w["text"].strip()) for w in near):
                return False, f"no header word for role {c['role']} at x~{c['header_x']}"
        rx = re.compile(cfg["ref_format"])
        ref_x = next(c["header_x"] for c in cols if c["role"] == "ref")
        nxt = min(
            (c["header_x"] for c in cols if c["header_x"] > ref_x), default=ref_x + 60
        )
        above = cfg["data_direction"] == "above"
        band = [
            w for w in words
            if ref_x - 8 <= w["x0"] < nxt
            and (cfg["header_top"] - w["top"] > 0) == above
        ]
        hits = (
            sum(1 for w in band if rx.fullmatch(w["text"].replace(" ", "")))
            + sum(1 for w in band if w["text"].strip() == "-")
        )
        if hits < 3:
            return False, f"ref regex matched only {hits} tokens"
        return True, "ok"
    except Exception as e:
        return False, f"validator error: {e}"


def _build_config(words, client, model: str) -> dict:
    cfg = _llm_discover(client, model, _build_layout_text(words))
    if cfg is not None:
        ok, why = _validate_config(cfg, words)
        if ok:
            cfg["_source"] = "llm"
            return cfg
        logger.warning(f"  [Stage1] LLM config rejected: {why}; using fallback.")
    cfg = _detect_header_fallback(words)
    cfg["_source"] = "fallback"
    return cfg


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 2 — Deterministic geometry cell extraction
# ═══════════════════════════════════════════════════════════════════════════════

def _reconstruct(line_chars: list, space_thr: float) -> str:
    line_chars = sorted(line_chars, key=lambda c: c["x0"])
    words, cur, prev_x1 = [], "", None
    for c in line_chars:
        t = c["text"]
        if t.isspace():
            if cur:
                words.append(cur)
                cur = ""
            prev_x1 = c["x1"]
            continue
        gap = 0 if prev_x1 is None else c["x0"] - prev_x1
        if cur and gap >= space_thr:
            words.append(cur)
            cur = ""
        cur += t
        prev_x1 = c["x1"]
    if cur:
        words.append(cur)
    return " ".join(words)


def _column_bounds(chars, cols_cfg, x_max, y_lo, y_hi) -> list:
    cols = sorted(
        [c for c in cols_cfg if c["role"] != "ignore"], key=lambda c: c["header_x"]
    )
    hx = [c["header_x"] for c in cols]
    region = [c for c in chars if y_lo < c["top"] < y_hi and c["x0"] < x_max]

    def valley(lo, hi):
        iv = sorted(
            (max(c["x0"], lo), min(c["x1"], hi))
            for c in region if c["x1"] > lo and c["x0"] < hi
        )
        merged = []
        for a, b in iv:
            if merged and a <= merged[-1][1] + 0.3:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        best = None
        for i in range(len(merged) - 1):
            g0, g1 = merged[i][1], merged[i + 1][0]
            if best is None or (g1 - g0) > best[0]:
                best = (g1 - g0, (g0 + g1) / 2)
        return best

    out = []
    for i, c in enumerate(cols):
        x_start = hx[0] - 6 if i == 0 else out[-1]["x_end"]
        if i < len(cols) - 1:
            g = valley(hx[i], hx[i + 1] + 25)
            x_end = g[1] if (g and g[0] >= 4) else (hx[i] + hx[i + 1]) / 2
        else:
            GUTTER = 12
            mc = sorted(
                [
                    ch for ch in chars
                    if ch["x0"] >= x_start and y_lo < ch["top"] < y_hi
                    and not ch["text"].isspace()
                ],
                key=lambda ch: ch["x0"],
            )
            if mc:
                edge = mc[0]["x1"]
                for ch in mc:
                    if ch["x0"] - edge > GUTTER:
                        break
                    edge = max(edge, ch["x1"])
                x_end = edge + 2
            else:
                x_end = x_max
        out.append({"role": c["role"], "x_start": x_start, "x_end": x_end})
    return out


def _data_y_extent(chars, cfg) -> tuple[float, float]:
    cols = sorted(
        [c for c in cfg["columns"] if c["role"] != "ignore"], key=lambda c: c["header_x"]
    )
    ref_x = next(c["header_x"] for c in cols if c["role"] == "ref")
    nxt = min(
        (c["header_x"] for c in cols if c["header_x"] > ref_x), default=ref_x + 40
    )
    bx0, bx1 = ref_x - 6, (ref_x + nxt) / 2
    above = cfg["data_direction"] == "above"
    htop = cfg["header_top"]
    bb = cfg["table_bbox"]
    default = (bb["y_min"], htop - 3) if above else (htop + 3, bb["y_max"])

    cand = [
        c for c in chars
        if bx0 <= c["x0"] < bx1 and not c["text"].isspace()
        and ((htop - c["top"]) > 3 if above else (c["top"] - htop) > 3)
    ]
    if not cand:
        return default

    cand.sort(key=lambda c: c["top"])
    med_w = statistics.median([c["x1"] - c["x0"] for c in cand])
    line_gap = max(2.0, 0.6 * med_w)
    space_thr = max(0.5, 0.25 * med_w)

    groups, cur, last = [], [], None
    for c in cand:
        if last is not None and c["top"] - last > line_gap:
            groups.append(cur)
            cur = []
        cur.append(c)
        last = c["top"]
    if cur:
        groups.append(cur)

    rx = re.compile(cfg.get("ref_format", _DEFAULT_REF_RE))
    anchors = [
        min(x["top"] for x in g)
        for g in groups if _is_ref(_reconstruct(g, space_thr), rx)
    ]
    if not anchors:
        return default

    anchors.sort(key=lambda y: (htop - y) if above else (y - htop))
    dist = sorted((htop - y) if above else (y - htop) for y in anchors)
    pitches = [dist[i + 1] - dist[i] for i in range(len(dist) - 1)]
    med_p = statistics.median(pitches) if pitches else 12

    kept = [anchors[0]]
    cur_d = (htop - anchors[0]) if above else (anchors[0] - htop)
    for y in anchors[1:]:
        d = (htop - y) if above else (y - htop)
        if d - cur_d > 2.5 * med_p + 3:
            break
        kept.append(y)
        cur_d = d

    far = max((htop - y) if above else (y - htop) for y in kept)
    return (htop - far - 3, htop - 3) if above else (htop + 3, htop + far + 3)


def _is_ref(text: str, rx) -> bool:
    t = text.strip()
    return t == "-" or bool(rx.fullmatch(t.replace(" ", "")))


def _is_zone_noise(text: str) -> bool:
    return text.strip() in set("ABCDEFGHIJ")


def _normalise_qty(q: str) -> str:
    if re.search(r"A\s*S\s*R\s*E\s*Q", q, re.I) or "REQD" in q.upper().replace(" ", ""):
        return "AS REQD."
    return q.strip()


def _extract_rows(chars, cfg) -> list:
    bbox = cfg["table_bbox"]
    x_max = bbox["x_max"]
    y_lo, y_hi = _data_y_extent(chars, cfg)
    cols = _column_bounds(chars, cfg["columns"], x_max, y_lo, y_hi)

    widths = [
        c["x1"] - c["x0"]
        for c in chars if y_lo < c["top"] < y_hi and not c["text"].isspace()
    ]
    med_w = statistics.median(widths) if widths else 3.0
    space_thr = max(0.5, 0.25 * med_w)
    line_gap  = max(2.0, 0.6 * med_w)

    lines_by = {}
    for col in cols:
        cc = sorted(
            [
                c for c in chars
                if col["x_start"] <= c["x0"] < col["x_end"] and y_lo < c["top"] < y_hi
            ],
            key=lambda c: c["top"],
        )
        groups, curg, last = [], [], None
        for c in cc:
            if last is not None and c["top"] - last > line_gap:
                groups.append(curg)
                curg = []
            curg.append(c)
            last = c["top"]
        if curg:
            groups.append(curg)
        lines_by[col["role"]] = [
            {"y": min(x["top"] for x in g), "text": _reconstruct(g, space_thr)}
            for g in groups
        ]

    for n in lines_by:
        if n != "ref":
            lines_by[n] = [l for l in lines_by[n] if not _is_zone_noise(l["text"])]

    rx = re.compile(cfg.get("ref_format", _DEFAULT_REF_RE))
    refs = sorted(
        [l for l in lines_by.get("ref", []) if _is_ref(l["text"], rx)],
        key=lambda l: l["y"],
    )
    others = [n for n in lines_by if n != "ref"]

    rows = []
    for i, r in enumerate(refs):
        y0 = r["y"] - 3
        y1 = refs[i + 1]["y"] - 3 if i + 1 < len(refs) else y_hi
        row = {"ref": r["text"].replace(" ", "")}
        for n in others:
            row[n] = " ".join(
                l["text"] for l in lines_by[n] if y0 <= l["y"] < y1
            ).strip()
        row["qty"] = _normalise_qty(row.get("qty", ""))
        rows.append(row)

    return [
        {
            "ref":         r.get("ref", ""),
            "description": r.get("description", ""),
            "qty":         r.get("qty", ""),
            "material":    r.get("material", ""),
        }
        for r in rows
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 3 — Confidence flags (debug metadata, stripped before saving)
# ═══════════════════════════════════════════════════════════════════════════════

def _validate_rows(rows: list) -> list:
    out = []
    for r in rows:
        flags = []
        if not r["ref"]:
            flags.append("empty_ref")
        if not r["description"]:
            flags.append("empty_description")
        q = r["qty"]
        if q and q != "AS REQD." and not q.isdigit():
            flags.append("qty_not_clean")
        if flags:
            logger.debug(f"  Row {r.get('ref', '?')!r} flags: {flags}")
        out.append({**r, "_confidence": max(round(1.0 - 0.25 * len(flags), 2), 0.0),
                    "_flags": flags})
    return out
