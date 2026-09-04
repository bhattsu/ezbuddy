"""Workbook and row validation helpers."""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from openpyxl.workbook.workbook import Workbook

from app.core.ingestion.excel.constants import EXPECTED_SHEETS, SHEET_COLUMN_MAP
from app.core.ingestion.excel.exceptions import MissingColumnError, MissingSheetError


def validate_workbook_sheets(workbook: Workbook) -> None:
    present = set(workbook.sheetnames)
    missing = EXPECTED_SHEETS - present
    if missing:
        extra = present - EXPECTED_SHEETS
        raise MissingSheetError(missing, extra)


def validate_sheet_headers(sheet_name: str, header_row: Sequence[Any]) -> dict[str, int]:
    """Return Excel header → column index (0-based). Raises if required columns missing."""
    column_map = SHEET_COLUMN_MAP[sheet_name]
    header_to_idx: dict[str, int] = {}
    for idx, cell in enumerate(header_row):
        if cell is None:
            continue
        key = str(cell).strip()
        if key:
            header_to_idx[key] = idx

    missing = set(column_map.keys()) - set(header_to_idx.keys())
    if missing:
        raise MissingColumnError(sheet_name, missing)
    return header_to_idx


def ignored_excel_columns(sheet_name: str, header_row: Sequence[Any]) -> list[str]:
    """Excel headers present on the sheet but not mapped for import (client extras)."""
    column_map = SHEET_COLUMN_MAP[sheet_name]
    known = set(column_map.keys())
    ignored: list[str] = []
    for cell in header_row:
        if cell is None:
            continue
        key = str(cell).strip()
        if key and key not in known:
            ignored.append(key)
    return ignored


def row_is_empty(values: Iterable[Any]) -> bool:
    for v in values:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return False
    return True


def clean_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def parse_yes_no(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().upper()
    if text in ("YES", "Y", "TRUE", "1"):
        return True
    if text in ("NO", "N", "FALSE", "0"):
        return False
    return None


def parse_int(value: Any) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except ValueError:
        return None
