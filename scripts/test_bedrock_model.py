"""Smoke-test that MODEL_ID in .env can invoke Bedrock successfully.

Usage (from repo root, venv active):
    python scripts/test_bedrock_model.py
    python scripts/test_bedrock_model.py --message "Say OK"
    python scripts/test_bedrock_model.py --log-level DEBUG
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import boto3
from botocore.config import Config

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import settings

logger = logging.getLogger("test_bedrock_model")

_ACCESS_DENIED_MARKERS = ("accessdenied", "not available for this account", "access denied")
_THROTTLE_MARKERS = ("throttling", "too many tokens", "rate exceeded", "429")
_INVALID_MODEL_MARKERS = ("model identifier is invalid", "validationexception")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )


def _classify_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(marker in text for marker in _ACCESS_DENIED_MARKERS):
        return (
            "ACCESS_DENIED — this MODEL_ID is not enabled for your AWS account/region. "
            "Fix: Bedrock console → Model access (same region as AWS_REGION), or change MODEL_ID in .env."
        )
    if any(marker in text for marker in _THROTTLE_MARKERS):
        return (
            "THROTTLED — daily/token quota exceeded. "
            "Fix: wait for quota reset or request a Bedrock quota increase."
        )
    if any(marker in text for marker in _INVALID_MODEL_MARKERS):
        return (
            "INVALID_MODEL_ID — MODEL_ID format or inference profile is wrong for this region. "
            "Fix: use a us./eu./apac. inference profile ID or a valid model ARN from Bedrock."
        )
    if "certificate verify failed" in text or "ssl" in text:
        return (
            "SSL_ERROR — local Python cannot verify AWS TLS certificates. "
            "Fix: install/update certifi, or set SSL_CERT_FILE to your CA bundle."
        )
    return f"FAILED — {exc}"


def _build_body(message: str) -> dict:
    return {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 256,
        "temperature": 0,
        "messages": [{"role": "user", "content": message}],
    }


def _invoke_sync(model_id: str, region: str, body: dict) -> dict:
    client = boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(read_timeout=120, connect_timeout=30, retries={"max_attempts": 2}),
    )
    response = client.invoke_model(
        modelId=model_id,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(response["body"].read())


async def run_test(message: str) -> int:
    model_id = str(settings.MODEL_ID or "").strip()
    region = str(settings.AWS_REGION or "").strip()

    logger.info("=== Bedrock MODEL_ID smoke test ===")
    logger.info("Region:   %s", region or "(not set)")
    logger.info("MODEL_ID: %s", model_id or "(not set)")

    if not model_id:
        logger.error("MODEL_ID is empty. Set it in .env and retry.")
        return 1
    if not region:
        logger.error("AWS_REGION is empty. Set it in .env and retry.")
        return 1

    body = _build_body(message)
    logger.info("INPUT (user message):\n%s", message)
    logger.info("INPUT (request body):\n%s", json.dumps(body, indent=2))

    try:
        loop = asyncio.get_event_loop()
        payload = await loop.run_in_executor(
            None, lambda: _invoke_sync(model_id, region, body)
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("RESULT: %s", _classify_error(exc))
        return 1

    usage = payload.get("usage") or {}
    content = payload.get("content") or []
    output_text = ""
    if content and isinstance(content[0], dict):
        output_text = str(content[0].get("text") or "").strip()

    logger.info("OUTPUT:\n%s", output_text or json.dumps(payload, indent=2))
    logger.info(
        "TOKENS: input=%s output=%s",
        usage.get("input_tokens", usage.get("inputTokens", 0)),
        usage.get("output_tokens", usage.get("outputTokens", 0)),
    )
    logger.info("RESULT: OK — MODEL_ID is working.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify Bedrock MODEL_ID from .env can invoke successfully."
    )
    parser.add_argument(
        "--message",
        default="Reply with exactly: Bedrock MODEL_ID is working.",
        help="Short prompt sent to the model",
    )
    parser.add_argument(
        "--log-level",
        default=settings.LOG_LEVEL,
        help="Logging level (default: LOG_LEVEL from .env)",
    )
    args = parser.parse_args()
    _configure_logging(args.log_level)
    raise SystemExit(asyncio.run(run_test(args.message)))


if __name__ == "__main__":
    main()
