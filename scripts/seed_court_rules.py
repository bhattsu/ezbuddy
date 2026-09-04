"""
Seed the three Texas divorce court-rules files into OpenSearch.

Usage (from repo root, with venv + .env OpenSearch/Bedrock configured):

    pip install opensearch-py requests-aws4auth
    python scripts/seed_court_rules.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.adapters.llm.bedrock import get_bedrock
from app.services.court_rules_service import CourtRulesService

SEED_FILES = [
    ("TX_FAQ.txt", "faq"),
    ("TX_DIVORCE_STANDARD_RULES.txt", "standard_rules"),
    ("TX_DIVORCE_STATEWIDE_RULE.txt", "statewide_rule"),
]


async def main() -> None:
    bedrock = get_bedrock()
    service = CourtRulesService(embedder=bedrock, llm_client=bedrock)
    print(
        f"Ingesting into vector_store={service.vector_store_name} "
        f"collection={service.collection_name}"
    )
    for file_name, doc_type in SEED_FILES:
        path = ROOT / file_name
        if not path.exists():
            print(f"SKIP missing: {path}")
            continue
        result = await service.ingest_file_bytes(
            file_bytes=path.read_bytes(),
            file_name=file_name,
            state_code="TX",
            case_type="divorce",
            doc_type=doc_type,
            replace_existing=True,
        )
        print(
            f"OK {file_name}: chunks={result['total_chunks']} "
            f"stored={result['vectors_stored']} source_key={result['source_key']}"
        )


if __name__ == "__main__":
    asyncio.run(main())
