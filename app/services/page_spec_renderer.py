"""
Deterministic page-spec → absolute-position HTML renderer.

The VLM supplies a structured page specification (coordinates in PDF points).
This module renders a fixed-size canvas page so layout is not left to free-form
CSS flow / letter-spacing guesses.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _s(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _font_stack(family: str) -> str:
    fam = (family or "").strip().strip('"').strip("'")
    lower = fam.lower()
    if "arial" in lower or "helvetica" in lower or "sans" in lower:
        return 'Arial, Helvetica, sans-serif'
    if "courier" in lower or "mono" in lower:
        return '"Courier New", Courier, monospace'
    if "georgia" in lower:
        return 'Georgia, "Times New Roman", Times, serif'
    if fam:
        return f'"{fam}", "Times New Roman", Times, serif'
    return '"Times New Roman", Times, serif'


def _css_color(value: Any, default: str = "#000000") -> str:
    color = _s(value, default).strip() or default
    if color.lower() in {"transparent", "none"}:
        return "transparent"
    if re.fullmatch(r"#?[0-9a-fA-F]{3,8}", color):
        return color if color.startswith("#") else f"#{color}"
    if re.fullmatch(r"rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)", color, flags=re.I):
        return color
    return default


def render_page_spec_to_html(page_spec: Dict[str, Any]) -> str:
    """Render one page_spec dict to a <section class='pdf-page'> fragment."""
    page_number = int(page_spec.get("page_number") or 1)
    width_pt = _f(page_spec.get("width_pt"), 612.0)
    height_pt = _f(page_spec.get("height_pt"), 792.0)
    background = _css_color(page_spec.get("background"), "#ffffff")
    elements = page_spec.get("elements") or []
    if not isinstance(elements, list):
        elements = []

    parts: List[str] = []
    for index, el in enumerate(elements):
        if not isinstance(el, dict):
            continue
        rendered = _render_element(el, index)
        if rendered:
            parts.append(rendered)

    return (
        f'<section class="pdf-page" data-page="{page_number}" data-locked="true" '
        f'style="position:relative;box-sizing:border-box;overflow:hidden;'
        f"width:{width_pt:.2f}pt;height:{height_pt:.2f}pt;"
        f"background:{background};color:#000;"
        f'letter-spacing:normal;word-spacing:normal;margin:0;padding:0;">'
        f"{''.join(parts)}</section>"
    )


def _render_element(el: Dict[str, Any], index: int) -> str:
    etype = _s(el.get("type"), "text").strip().lower()
    eid = html_lib.escape(_s(el.get("id"), f"e{index:03d}"))
    x = _f(el.get("x"))
    y = _f(el.get("y"))
    w = max(0.5, _f(el.get("width"), 10.0))
    h = max(0.5, _f(el.get("height"), 10.0))
    color = _css_color(el.get("color"), "#000000")

    base = (
        f'id="{eid}" data-type="{html_lib.escape(etype)}" '
        f'style="position:absolute;left:{x:.2f}pt;top:{y:.2f}pt;'
        f"width:{w:.2f}pt;height:{h:.2f}pt;box-sizing:border-box;"
        f'letter-spacing:normal;word-spacing:normal;margin:0;padding:0;'
    )

    if etype == "line":
        thickness = max(0.4, _f(el.get("thickness"), _f(el.get("height"), 1.0)))
        # Treat mostly-horizontal if width >= height.
        if w >= h:
            return (
                f"<div {base}border:none;background:{color};"
                f'height:{thickness:.2f}pt;"></div>'
            )
        return (
            f"<div {base}border:none;background:{color};"
            f'width:{thickness:.2f}pt;"></div>'
        )

    if etype == "box":
        thickness = max(0.5, _f(el.get("thickness"), 1.0))
        fill = _css_color(el.get("fill_color"), "transparent")
        bg = "transparent" if fill == "transparent" else fill
        return (
            f"<div {base}border:{thickness:.2f}pt solid {color};"
            f'background:{bg};"></div>'
        )

    if etype == "checkbox":
        checked = el.get("checked") in (True, "true", "True", 1, "1", "yes", "YES")
        mark = "✓" if checked else ""
        label = _s(el.get("label"))
        fillable = "true" if el.get("fillable", True) else "false"
        return (
            f"<div {base}border:1pt solid #000;background:#fff;"
            f"display:flex;align-items:center;justify-content:center;"
            f'font-family:Arial,Helvetica,sans-serif;font-size:{max(7.0, h * 0.8):.1f}pt;'
            f'font-weight:700;color:#000;" data-fillable="{fillable}" '
            f'data-label="{html_lib.escape(label)}" data-checked="{str(checked).lower()}">'
            f"{mark}</div>"
        )

    if etype == "image":
        # Keep geometry; optional alt from text/label.
        alt = html_lib.escape(_s(el.get("text") or el.get("label") or "image"))
        return (
            f"<div {base}border:0;background:transparent;overflow:hidden;\" "
            f'title="{alt}"></div>'
        )

    # Default: text / field
    text = _s(el.get("text"))
    font_size = max(6.0, _f(el.get("font_size"), 10.0))
    line_height = _f(el.get("line_height"), font_size * 1.2)
    if line_height < font_size:
        line_height = font_size * 1.15
    weight = _s(el.get("font_weight"), "normal") or "normal"
    style = _s(el.get("font_style"), "normal") or "normal"
    align = _s(el.get("align"), "left") or "left"
    if align not in {"left", "center", "right", "justify"}:
        align = "left"
    # Avoid justify rivers unless explicitly needed; still allow if requested.
    fillable = bool(el.get("fillable"))
    label = _s(el.get("label"))
    family = _font_stack(_s(el.get("font_family")))
    safe = html_lib.escape(text).replace("\n", "<br/>")
    return (
        f"<div {base}border:none;background:transparent;overflow:hidden;"
        f"font-family:{family};font-size:{font_size:.1f}pt;"
        f"font-weight:{html_lib.escape(weight)};font-style:{html_lib.escape(style)};"
        f"line-height:{line_height:.1f}pt;color:{color};text-align:{align};"
        f'white-space:pre-wrap;" data-fillable="{str(fillable).lower()}" '
        f'data-label="{html_lib.escape(label)}">{safe}</div>'
    )


def extract_page_spec(llm_output: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pull a page_spec dict from structured or raw LLM/VLM output."""
    if not isinstance(llm_output, dict):
        return None

    candidates: List[Any] = [llm_output]
    if isinstance(llm_output.get("result"), dict):
        candidates.append(llm_output["result"])
    for key in ("page_spec", "page", "specification", "output"):
        if key in llm_output:
            candidates.append(llm_output.get(key))
        nested = llm_output.get("result")
        if isinstance(nested, dict) and key in nested:
            candidates.append(nested.get(key))

    for cand in candidates:
        spec = _coerce_page_spec(cand)
        if spec:
            return spec
    return None


def _coerce_page_spec(value: Any) -> Optional[Dict[str, Any]]:
    import json

    if isinstance(value, dict) and isinstance(value.get("elements"), list):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            chunk = text[start : end + 1]
            try:
                parsed = json.loads(chunk)
                if isinstance(parsed, dict) and isinstance(parsed.get("elements"), list):
                    return parsed
            except json.JSONDecodeError:
                repaired = _repair_truncated_page_spec(text[start:])
                if repaired:
                    logger.warning(
                        "[page_spec] Recovered truncated JSON with %d element(s)",
                        len(repaired.get("elements") or []),
                    )
                    return repaired
        else:
            # Truncated mid-stream: no closing brace yet.
            if start != -1:
                repaired = _repair_truncated_page_spec(text[start:])
                if repaired:
                    logger.warning(
                        "[page_spec] Recovered open truncated JSON with %d element(s)",
                        len(repaired.get("elements") or []),
                    )
                    return repaired
    return None


def _repair_truncated_page_spec(text: str) -> Optional[Dict[str, Any]]:
    """
    Salvage a page_spec when the model hits max_tokens mid-JSON.

    Strategy: keep complete element objects inside the elements array and close
    the JSON structure so rendering can proceed with partial coverage.
    """
    import json

    if not text or '"elements"' not in text:
        return None

    # Pull simple top-level scalars when present.
    def _num(name: str, default: float) -> float:
        m = re.search(rf'"{name}"\s*:\s*([0-9]+(?:\.[0-9]+)?)', text)
        return float(m.group(1)) if m else default

    def _str(name: str, default: str) -> str:
        m = re.search(rf'"{name}"\s*:\s*"([^"]*)"', text)
        return m.group(1) if m else default

    arr_match = re.search(r'"elements"\s*:\s*\[', text)
    if not arr_match:
        return None
    i = arr_match.end()
    elements: List[Dict[str, Any]] = []
    n = len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n,":
            i += 1
        if i >= n or text[i] == "]":
            break
        if text[i] != "{":
            break
        depth = 0
        in_str = False
        esc = False
        start_obj = i
        j = i
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        obj_text = text[start_obj : j + 1]
                        try:
                            obj = json.loads(obj_text)
                            if isinstance(obj, dict):
                                elements.append(obj)
                        except json.JSONDecodeError:
                            pass
                        i = j + 1
                        break
            j += 1
        else:
            # Incomplete trailing object — stop.
            break
        if j >= n and depth != 0:
            break

    if not elements:
        return None

    return {
        "page_number": int(_num("page_number", 1)),
        "width_pt": _num("width_pt", 612.0),
        "height_pt": _num("height_pt", 792.0),
        "orientation": _str("orientation", "portrait"),
        "background": _str("background", "#ffffff"),
        "elements": elements,
        "truncated": True,
    }
