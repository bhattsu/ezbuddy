"""Parse and validate Excel pipe-string config for API-driven question options."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.core.ingestion.excel.schemas import ExcelPackage

API_SOURCE = "api"
REQUIRED_API_KEYS = ("endpoint", "param_from", "param_name", "value_field", "label_field")
_ENDPOINT_PATH_RE = re.compile(r"^/[a-zA-Z0-9_./-]+$")


def normalize_api_endpoint(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise ValueError("API endpoint is empty")

    if text.startswith("http://") or text.startswith("https://"):
        path = urlparse(text).path or "/"
        return path if path.startswith("/") else f"/{path}"

    return text if text.startswith("/") else f"/{text}"


def is_api_options_pipe(raw: str | None) -> bool:
    return bool(raw and raw.strip().lower().startswith("source:api"))


def parse_api_options_pipe(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not is_api_options_pipe(text):
        return None

    fields: dict[str, str] = {}
    for segment in text.split("|"):
        segment = segment.strip()
        if not segment or ":" not in segment:
            continue
        key, _, value = segment.partition(":")
        fields[key.strip().lower()] = value.strip()

    if fields.get("source", "").lower() != API_SOURCE:
        return None

    missing = [k for k in REQUIRED_API_KEYS if not fields.get(k)]
    if missing:
        raise ValueError(f"API options config missing required keys: {', '.join(missing)}")

    return {
        "options_source": API_SOURCE,
        "endpoint": normalize_api_endpoint(fields["endpoint"]),
        "param_from": fields["param_from"].upper(),
        "param_name": fields["param_name"],
        "value_field": fields["value_field"],
        "label_field": fields["label_field"],
    }


def validate_question_options_config(pkg: ExcelPackage) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    question_codes = {q.question_code for q in pkg.questions}

    for row in pkg.questions:
        raw = (row.validation or "").strip()
        if not is_api_options_pipe(raw):
            continue

        code = row.question_code
        try:
            cfg = parse_api_options_pipe(raw)
        except ValueError as exc:
            errors.append(f"Question {code!r}: {exc}")
            continue

        if cfg is None:
            continue

        if cfg["param_from"] not in question_codes:
            errors.append(
                f"Question {code!r}: API options param_from {cfg['param_from']!r} "
                "is not a question_code in Question Library"
            )

        if row.lookup_code and str(row.lookup_code).strip():
            warnings.append(
                f"Question {code!r}: dynamic API options configured but lookup_code "
                f"{row.lookup_code!r} is set; lookup lists are ignored for this question"
            )

        qtype = (row.input_type or "").strip().lower()
        if qtype and qtype not in ("dropdown", "select", "radio"):
            warnings.append(
                f"Question {code!r}: API options are typically used with dropdown/select "
                f"(input_type={row.input_type!r})"
            )

        if not _ENDPOINT_PATH_RE.match(cfg["endpoint"]):
            errors.append(f"Question {code!r}: invalid API endpoint {cfg['endpoint']!r}")

    return errors, warnings
