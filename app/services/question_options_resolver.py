"""Resolve question dropdown options from DB rules (static lookup or external API)."""

from __future__ import annotations

from typing import Any

from app.adapters.data_sources.AIM_rds import RDSRepository
from app.services.uslegalpro_api_client import USLegalProApiClient


async def load_question_validation_rules(
    question_code: str,
    *,
    rds: RDSRepository | None = None,
) -> dict[str, Any] | None:
    if rds is None:
        return None

    code = question_code.strip()
    row = await rds.fetch_one(
        "SELECT validation_rules FROM configuration.questions WHERE code = $1",
        code,
    )
    if row is None and code.upper() != code:
        row = await rds.fetch_one(
            "SELECT validation_rules FROM configuration.questions WHERE code = $1",
            code.upper(),
        )
    if row is None or not row.get("validation_rules"):
        return None
    rules = row["validation_rules"]
    return dict(rules) if isinstance(rules, dict) else None


def _parent_answer(answers: dict[str, str], param_from: str) -> str | None:
    key = str(param_from)
    for candidate in (key, key.upper(), key.lower()):
        value = answers.get(candidate)
        if value:
            return str(value)
    return None


def static_options_from_rules(rules: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in rules.get("options") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        if value is None:
            continue
        label = item.get("label") or item.get("display_label") or value
        out.append({"value": str(value), "label": str(label)})
    return out


def _options_source_label(rules: dict[str, Any]) -> str:
    if rules.get("options_source") == "api":
        return "api"
    if rules.get("lookup_code"):
        return "lookup"
    return "static"


async def resolve_options_from_rules(
    rules: dict[str, Any],
    answers: dict[str, str],
    *,
    api_client: USLegalProApiClient | None = None,
) -> list[dict[str, str]]:
    if rules.get("options_source") == "api":
        return await _resolve_api_options(rules, answers, api_client=api_client)
    return static_options_from_rules(rules)


async def _resolve_api_options(
    rules: dict[str, Any],
    answers: dict[str, str],
    *,
    api_client: USLegalProApiClient | None = None,
) -> list[dict[str, str]]:
    endpoint = rules.get("endpoint")
    param_from = rules.get("param_from")
    param_name = rules.get("param_name")
    value_field = rules.get("value_field") or "code"
    label_field = rules.get("label_field") or "name"

    if not endpoint or not param_from or not param_name:
        raise ValueError("API options rules are incomplete (endpoint, param_from, param_name)")

    parent_value = _parent_answer(answers, str(param_from))
    if not parent_value:
        raise ValueError(f"Missing answer for parent question {param_from!r} (required for API options)")

    client = api_client or USLegalProApiClient()
    payload = await client.get_json(str(endpoint), {str(param_name): parent_value})

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("items") or payload.get("data") or payload.get("results") or []
    else:
        items = []

    options: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_value = item.get(value_field)
        if raw_value is None:
            continue
        raw_label = item.get(label_field)
        if raw_label is None:
            raw_label = raw_value
        options.append({"value": str(raw_value), "label": str(raw_label)})
    return options


async def resolve_question_options(
    question_code: str,
    answers: dict[str, str],
    *,
    rds: RDSRepository | None = None,
    api_client: USLegalProApiClient | None = None,
) -> tuple[str, list[dict[str, str]]]:
    if rds is None:
        raise RuntimeError("RDS is not configured")

    rules = await load_question_validation_rules(question_code, rds=rds)
    if rules is None:
        raise LookupError(f"Question not found: {question_code}")

    source = _options_source_label(rules)
    options = await resolve_options_from_rules(rules, answers, api_client=api_client)
    return source, options
