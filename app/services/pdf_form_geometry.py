"""
PDF-native fill-slot detection for court forms.

VLM percentage guessing is inaccurate for overlays. Instead:
1) Detect fill targets from PDF geometry (AcroForm widgets, underscore runs,
   horizontal rule lines near labels, checkbox glyphs/squares).
2) LLM only maps user data → slot ids (semantic matching).
3) Values are inserted at exact PDF coordinates, then the page is rendered.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pymupdf

logger = logging.getLogger(__name__)

try:
    pymupdf.TOOLS.set_small_glyph_heights(True)
except Exception:
    pass

_UNDERSCORE_RE = re.compile(r"^[\s_\.\-–—]{2,}$")
_CHECKBOX_CHARS = set("☐☑☒□■❑❒✓✔✕")


@dataclass
class FillSlot:
    slot_id: str
    page_index: int
    kind: str  # text | checkbox | digit
    x0: float
    y0: float
    x1: float
    y1: float
    label: str
    context: str
    font_size: float = 10.0

    @property
    def rect(self) -> Tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    def to_prompt_dict(self) -> Dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "kind": self.kind,
            "label": self.label,
            "context": self.context[:180],
        }


def extract_page_slots(page: pymupdf.Page, page_index: int) -> List[FillSlot]:
    """Detect fillable slots on one PDF page using native geometry."""
    slots: List[FillSlot] = []
    page_rect = page.rect
    words = page.get_text("words") or []
    # words: (x0, y0, x1, y1, word, block_no, line_no, word_no)
    word_items = [
        {
            "x0": float(w[0]),
            "y0": float(w[1]),
            "x1": float(w[2]),
            "y1": float(w[3]),
            "text": str(w[4]),
            "block": int(w[5]),
            "line": int(w[6]),
            "word": int(w[7]),
        }
        for w in words
        if len(w) >= 8 and str(w[4]).strip()
    ]

    # --- 1) AcroForm widgets (most accurate when present) ---
    try:
        widgets = list(page.widgets() or [])
    except Exception:
        widgets = []
    for i, widget in enumerate(widgets):
        rect = widget.rect
        if rect.width < 2 or rect.height < 2:
            continue
        field_type = getattr(widget, "field_type_string", "") or ""
        name = (widget.field_name or f"widget_{i}").strip()
        kind = "checkbox" if "check" in field_type.lower() else "text"
        slots.append(
            FillSlot(
                slot_id=f"p{page_index}_acro_{i}",
                page_index=page_index,
                kind=kind,
                x0=float(rect.x0),
                y0=float(rect.y0),
                x1=float(rect.x1),
                y1=float(rect.y1),
                label=name,
                context=name,
                font_size=max(8.0, min(12.0, float(rect.height) * 0.75)),
            )
        )
    if slots:
        logger.info(
            "[PDF Slots] page %d: %d AcroForm widget slot(s)",
            page_index + 1,
            len(slots),
        )
        return _dedupe_slots(slots)

    # --- 2) Underscore / dotted blank spans from text layer ---
    underscore_slots = _slots_from_underscore_spans(page, page_index, word_items)
    slots.extend(underscore_slots)

    # --- 3) Horizontal drawing lines as blanks (right of / under labels) ---
    h_lines = _horizontal_lines(page)
    line_slots = _slots_from_lines(page_index, word_items, h_lines, page_rect)
    slots.extend(line_slots)

    # --- 4) Checkbox glyphs / small squares near option labels ---
    check_slots = _slots_from_checkboxes(page, page_index, word_items)
    slots.extend(check_slots)

    slots = _dedupe_slots(slots)
    # Prefer slots that look like real fill areas (not tiny noise)
    slots = [
        s
        for s in slots
        if (s.x1 - s.x0) >= 8 and (s.y1 - s.y0) >= 4
    ]
    logger.info(
        "[PDF Slots] page %d: %d geometry slot(s) "
        "(underscores=%d lines=%d checks=%d)",
        page_index + 1,
        len(slots),
        len(underscore_slots),
        len(line_slots),
        len(check_slots),
    )
    return slots


def apply_fills_to_page(
    page: pymupdf.Page,
    slots: Sequence[FillSlot],
    fills: Sequence[Dict[str, Any]],
) -> int:
    """Insert mapped values into the page at exact PDF coordinates. Returns count."""
    by_id = {
        str(f.get("slot_id")): f
        for f in fills
        if isinstance(f, dict) and f.get("slot_id")
    }
    applied = 0
    for slot in slots:
        payload = by_id.get(slot.slot_id)
        if not payload:
            continue
        rect = pymupdf.Rect(slot.x0, slot.y0, slot.x1, slot.y1)
        # Keep text inside the blank; pad slightly inward.
        inset = min(1.5, rect.width * 0.02, rect.height * 0.15)
        rect = pymupdf.Rect(
            rect.x0 + inset,
            rect.y0 + inset * 0.3,
            rect.x1 - inset,
            rect.y1 - inset * 0.2,
        )
        if slot.kind == "checkbox":
            checked = payload.get("checked")
            if checked in (True, "true", "True", 1, "1", "yes", "YES"):
                fontsize = max(7.0, min(14.0, rect.height * 0.85))
                # Center a checkmark in the box.
                page.insert_textbox(
                    rect,
                    "✓",
                    fontsize=fontsize,
                    fontname="helv",
                    color=(0, 0, 0),
                    align=pymupdf.TEXT_ALIGN_CENTER,
                )
                applied += 1
            continue

        value = payload.get("value")
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if slot.kind == "digit":
            text = text[:1]

        fontsize = float(slot.font_size or 10.0)
        fontsize = max(7.0, min(14.0, fontsize))
        # Shrink until text fits the blank width.
        for _ in range(8):
            rc = page.insert_textbox(
                rect,
                text,
                fontsize=fontsize,
                fontname="times",  # Times-Roman
                color=(0, 0, 0),
                align=pymupdf.TEXT_ALIGN_LEFT,
            )
            if rc >= 0:
                applied += 1
                break
            fontsize = max(6.0, fontsize - 0.8)
        else:
            # Last resort: baseline insert at left of blank.
            page.insert_text(
                (rect.x0, min(rect.y1 - 1.0, rect.y0 + fontsize)),
                text[:80],
                fontsize=fontsize,
                fontname="times",
                color=(0, 0, 0),
            )
            applied += 1
    return applied


def fill_and_render_page(
    pdf_bytes: bytes,
    page_index: int,
    slots: Sequence[FillSlot],
    fills: Sequence[Dict[str, Any]],
    dpi: int = 200,
) -> Tuple[str, float, float]:
    """
    Copy one page, insert fills at PDF coords, render PNG base64.
    Returns (image_b64, width_in, height_in).
    """
    import base64

    src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        dst = pymupdf.open()
        try:
            dst.insert_pdf(src, from_page=page_index, to_page=page_index)
            page = dst[0]
            # Remap slots to this single-page doc (same local coords).
            applied = apply_fills_to_page(page, slots, fills)
            logger.info(
                "[PDF Fill] page %d applied %d/%d fill(s)",
                page_index + 1,
                applied,
                len(fills),
            )
            width_in = float(page.rect.width) / 72.0
            height_in = float(page.rect.height) / 72.0
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            img_b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
            return img_b64, width_in, height_in
        finally:
            dst.close()
    finally:
        src.close()


def extract_slots_from_pdf_bytes(pdf_bytes: bytes, page_index: int) -> List[FillSlot]:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return extract_page_slots(doc[page_index], page_index)
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Internal geometry helpers
# ---------------------------------------------------------------------------


def _horizontal_lines(page: pymupdf.Page) -> List[Tuple[float, float, float]]:
    """Return (y, x0, x1) for near-horizontal strokes/rules."""
    lines: List[Tuple[float, float, float]] = []
    try:
        drawings = page.get_drawings() or []
    except Exception:
        return lines
    for drawing in drawings:
        for item in drawing.get("items") or []:
            op = item[0]
            if op == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) <= 1.2 and abs(p1.x - p2.x) >= 18:
                    y = (p1.y + p2.y) / 2.0
                    lines.append((y, min(p1.x, p2.x), max(p1.x, p2.x)))
            elif op == "re":
                rect = item[1]
                # Bottom edge of thin rectangles often acts as an underline.
                if rect.width >= 18 and rect.height <= 4:
                    lines.append((float(rect.y1), float(rect.x0), float(rect.x1)))
                elif rect.width >= 18 and 4 < rect.height <= 22:
                    # Open input box: use inner bottom band as write line.
                    lines.append(
                        (float(rect.y1) - 1.0, float(rect.x0) + 1.0, float(rect.x1) - 1.0)
                    )
    lines.sort(key=lambda t: (t[0], t[1]))
    return _merge_collinear_lines(lines)


def _merge_collinear_lines(
    lines: List[Tuple[float, float, float]], y_tol: float = 1.0
) -> List[Tuple[float, float, float]]:
    if not lines:
        return []
    merged: List[Tuple[float, float, float]] = []
    cy, cx0, cx1 = lines[0]
    for y, x0, x1 in lines[1:]:
        if abs(y - cy) <= y_tol and x0 <= cx1 + 4:
            cx1 = max(cx1, x1)
            cy = (cy + y) / 2.0
        else:
            merged.append((cy, cx0, cx1))
            cy, cx0, cx1 = y, x0, x1
    merged.append((cy, cx0, cx1))
    return merged


def _slots_from_underscore_spans(
    page: pymupdf.Page,
    page_index: int,
    word_items: List[Dict[str, Any]],
) -> List[FillSlot]:
    slots: List[FillSlot] = []
    try:
        blocks = page.get_text("dict").get("blocks") or []
    except Exception:
        blocks = []

    idx = 0
    for block in blocks:
        for line in block.get("lines") or []:
            spans = line.get("spans") or []
            for span in spans:
                text = (span.get("text") or "").strip()
                if not text or not _UNDERSCORE_RE.match(text.replace(" ", "")):
                    continue
                bbox = span.get("bbox") or [0, 0, 0, 0]
                x0, y0, x1, y1 = map(float, bbox)
                width = x1 - x0
                if width < 10:
                    continue
                label = _nearest_label(word_items, x0, y0, x1, y1)
                # Narrow consecutive underscore cells → digit boxes
                kind = "digit" if width < 14 else "text"
                fontsize = float(span.get("size") or 10.0)
                slots.append(
                    FillSlot(
                        slot_id=f"p{page_index}_us_{idx}",
                        page_index=page_index,
                        kind=kind,
                        x0=x0,
                        y0=y0 - max(1.0, (y1 - y0) * 0.15),
                        x1=x1,
                        y1=y1,
                        label=label,
                        context=label,
                        font_size=max(7.0, min(12.0, fontsize)),
                    )
                )
                idx += 1
    return slots


def _slots_from_lines(
    page_index: int,
    word_items: List[Dict[str, Any]],
    h_lines: List[Tuple[float, float, float]],
    page_rect: pymupdf.Rect,
) -> List[FillSlot]:
    slots: List[FillSlot] = []
    for i, (y, x0, x1) in enumerate(h_lines):
        width = x1 - x0
        if width < 24:
            continue
        # Skip full-page rules / heavy section dividers near margins.
        if width > page_rect.width * 0.92:
            continue
        label = _nearest_label(word_items, x0, y - 14, x1, y)
        # Write above the rule line.
        height = 11.0
        slots.append(
            FillSlot(
                slot_id=f"p{page_index}_ln_{i}",
                page_index=page_index,
                kind="text",
                x0=x0 + 1.0,
                y0=max(0.0, y - height),
                x1=x1 - 1.0,
                y1=y - 0.5,
                label=label,
                context=label,
                font_size=10.0,
            )
        )
    return slots


def _slots_from_checkboxes(
    page: pymupdf.Page,
    page_index: int,
    word_items: List[Dict[str, Any]],
) -> List[FillSlot]:
    slots: List[FillSlot] = []
    idx = 0
    # Checkbox characters in text layer
    try:
        blocks = page.get_text("dict").get("blocks") or []
    except Exception:
        blocks = []
    for block in blocks:
        for line in block.get("lines") or []:
            for span in line.get("spans") or []:
                text = span.get("text") or ""
                if not any(ch in _CHECKBOX_CHARS for ch in text):
                    continue
                bbox = span.get("bbox") or [0, 0, 0, 0]
                x0, y0, x1, y1 = map(float, bbox)
                label = _nearest_label(word_items, x1, y0, x1 + 180, y1, prefer_right=True)
                slots.append(
                    FillSlot(
                        slot_id=f"p{page_index}_cb_{idx}",
                        page_index=page_index,
                        kind="checkbox",
                        x0=x0,
                        y0=y0,
                        x1=max(x1, x0 + 8),
                        y1=max(y1, y0 + 8),
                        label=label,
                        context=label,
                        font_size=9.0,
                    )
                )
                idx += 1

    # Small square drawings as checkbox outlines
    try:
        drawings = page.get_drawings() or []
    except Exception:
        drawings = []
    for drawing in drawings:
        for item in drawing.get("items") or []:
            if item[0] != "re":
                continue
            rect = item[1]
            w, h = float(rect.width), float(rect.height)
            if 5 <= w <= 14 and 5 <= h <= 14 and abs(w - h) <= 3:
                label = _nearest_label(
                    word_items,
                    float(rect.x1),
                    float(rect.y0),
                    float(rect.x1) + 200,
                    float(rect.y1),
                    prefer_right=True,
                )
                slots.append(
                    FillSlot(
                        slot_id=f"p{page_index}_sq_{idx}",
                        page_index=page_index,
                        kind="checkbox",
                        x0=float(rect.x0),
                        y0=float(rect.y0),
                        x1=float(rect.x1),
                        y1=float(rect.y1),
                        label=label,
                        context=label,
                        font_size=9.0,
                    )
                )
                idx += 1
    return slots


def _nearest_label(
    word_items: List[Dict[str, Any]],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    prefer_right: bool = False,
) -> str:
    """Pick nearby printed words as the slot label/context."""
    cy = (y0 + y1) / 2.0
    candidates: List[Tuple[float, Dict[str, Any]]] = []
    for w in word_items:
        text = w["text"]
        if _UNDERSCORE_RE.match(text.replace(" ", "")):
            continue
        if any(ch in _CHECKBOX_CHARS for ch in text):
            continue
        wcy = (w["y0"] + w["y1"]) / 2.0
        if prefer_right:
            if w["x0"] < x0 - 2:
                continue
            if abs(wcy - cy) > 10:
                continue
            dist = (w["x0"] - x1) + abs(wcy - cy) * 2
        else:
            # Prefer label left on same line, else above overlapping x.
            same_line = abs(wcy - cy) <= 9 and w["x1"] <= x0 + 4
            above = (y0 - 22) <= w["y1"] <= (y0 + 2) and not (
                w["x1"] < x0 - 40 or w["x0"] > x1 + 40
            )
            if not (same_line or above):
                continue
            dist = abs(wcy - cy) * 3 + abs(((w["x0"] + w["x1"]) / 2) - x0) * 0.05
            if same_line:
                dist -= 20
        candidates.append((dist, w))
    if not candidates:
        return ""
    candidates.sort(key=lambda t: t[0])
    # Gather a short phrase from the best line cluster.
    best = candidates[0][1]
    same = [
        w
        for _, w in candidates[:12]
        if abs(((w["y0"] + w["y1"]) / 2) - ((best["y0"] + best["y1"]) / 2)) <= 6
    ]
    same.sort(key=lambda w: w["x0"])
    phrase = " ".join(w["text"] for w in same[:10]).strip()
    return phrase[:120]


def _dedupe_slots(slots: List[FillSlot]) -> List[FillSlot]:
    """Drop heavily overlapping slots (keep larger / labeled ones)."""
    if not slots:
        return []
    ordered = sorted(
        slots,
        key=lambda s: (
            0 if s.label else 1,
            -( (s.x1 - s.x0) * (s.y1 - s.y0) ),
            s.y0,
            s.x0,
        ),
    )
    kept: List[FillSlot] = []
    for slot in ordered:
        overlap = False
        for other in kept:
            if _iou(slot.rect, other.rect) > 0.55:
                overlap = True
                break
        if not overlap:
            kept.append(slot)
    kept.sort(key=lambda s: (s.y0, s.x0))
    # Re-id stably after sort for cleaner prompts.
    for i, slot in enumerate(kept):
        prefix = {"text": "t", "checkbox": "c", "digit": "d"}.get(slot.kind, "s")
        slot.slot_id = f"p{slot.page_index}_{prefix}{i}"
    return kept


def _iou(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def slots_for_prompt(slots: Sequence[FillSlot]) -> List[Dict[str, Any]]:
    return [s.to_prompt_dict() for s in slots]
