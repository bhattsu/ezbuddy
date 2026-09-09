"""
Standalone driver for the ``/v2/{state}/efile`` payload.

Walks the live US Legal Pro stage API for a selected jurisdiction / category /
case type, prints the ``CodeBundle`` returned, builds a synthetic new-case
payload, and runs :func:`validate_new_case_payload` against it. Optionally
POSTs the payload to ``/efile`` behind ``--submit``.

Usage examples:

    # dry run for the guide's Divorce No Children (Harris County) case
    python scripts/test_efile_payload.py \
        --state tx --jurisdiction harris:dc \
        --case-category 131370 --case-type 209421 \
        --filing "code=209523,doc_type=53689,file=https://example.test/complaint.pdf,file_name=complaint.pdf,description=Petition,size=123261"

    # multi-filing dry run
    python scripts/test_efile_payload.py --state tx --jurisdiction harris:dc \
        --case-category 131370 --case-type 209421 \
        --filing "code=209523,doc_type=53689,file=https://example.test/complaint.pdf,file_name=complaint.pdf,description=Petition,size=123261" \
        --filing "code=209524,doc_type=53690,file=https://example.test/exhibit.pdf,file_name=exhibit.pdf,description=Exhibit A,size=42000"

    # actually submit the resulting payload (requires a real payment account id)
    python scripts/test_efile_payload.py --state tx --jurisdiction harris:dc \
        --case-category 131370 --case-type 209421 \
        --filing "code=209523,doc_type=53689,file=https://.../complaint.pdf,file_name=complaint.pdf,description=Petition,size=123261" \
        --payment-account-id 013af35d-66f2-4aa8-85bc-d3aecf8c4443 \
        --submit

Environment (read from ``.env``):
    USLEGALPRO_API_BASE_URL   (defaults to app.config.settings default)
    USLEGALPRO_CLIENT_TOKEN
    USLEGALPRO_AUTH_TOKEN     (format: user_id/client_token/session_id)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.adapters.uslegalpro.tokens import client_token_from_auth_token  # noqa: E402
from app.config.settings import settings  # noqa: E402
from app.services.efile_mapping_service import (  # noqa: E402
    assemble_new_case_efile_data_live,
)
from app.services.efile_validator import (  # noqa: E402
    EFilePayloadValidationError,
    validate_new_case_payload,
)
from app.services.uslegalpro_api_client import USLegalProApiClient  # noqa: E402
from app.services.uslegalpro_codes_service import (  # noqa: E402
    CodeBundle,
    USLegalProCodesService,
)
from app.services.uslegalpro_efile_service import (  # noqa: E402
    USLegalProEFileService,
    draft_reference_id,
)

logger = logging.getLogger("scripts.test_efile_payload")


# ---------------------------------------------------------------------------
# CLI parsing


def _parse_filing_spec(spec: str) -> Dict[str, Any]:
    """Parse ``key=value,key=value`` -> dict; ``size`` is coerced to int."""
    row: Dict[str, Any] = {}
    for chunk in (spec or "").split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        row[key.strip()] = value.strip()
    if "size" in row:
        try:
            row["size"] = int(row["size"])
        except (TypeError, ValueError):
            pass  # keep as-is; the validator will flag it
    row.setdefault("associated_parties", [])
    return row


def _parse_party_spec(spec: str) -> Dict[str, Any]:
    row = _parse_filing_spec(spec)
    if "is_business" in row:
        row["is_business"] = str(row["is_business"]).strip().lower() in {
            "true",
            "yes",
            "1",
        }
    else:
        row.setdefault("is_business", False)
    row.setdefault("additional_attorneys", [])
    return row


def _build_default_parties(state: str) -> List[Dict[str, Any]]:
    """Return the JANE / JOHN DOE sample from ``efile-payload-guide.md``."""
    return [
        {
            "id": "Party_1",
            "country": "US",
            "city": "Houston",
            "state": state.upper(),
            "zip_code": "77002",
            "address_line_1": "1200 Baker Street",
            "first_name": "JANE",
            "last_name": "DOE",
            "is_business": False,
            "lead_attorney": "PRO SE",
            "additional_attorneys": [],
        },
        {
            "id": "Party_2",
            "country": "US",
            "first_name": "JOHN",
            "last_name": "DOE",
            "is_business": False,
            "additional_attorneys": [],
        },
    ]


def _apply_party_types_to_defaults(
    parties: List[Dict[str, Any]],
    party_types: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Fill ``type`` for the two default parties from the party-type list."""
    if not parties or not party_types:
        return parties
    codes = [
        str((row or {}).get("code") or "").strip()
        for row in party_types
        if str((row or {}).get("code") or "").strip()
    ]
    if not codes:
        return parties
    for idx, party in enumerate(parties):
        if not party.get("type"):
            party["type"] = codes[idx] if idx < len(codes) else codes[-1]
    return parties


# ---------------------------------------------------------------------------
# printing helpers


def _dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def _print_bundle_summary(bundle: CodeBundle) -> None:
    print("=" * 72)
    print("CodeBundle (live):")
    print("-" * 72)
    print(f"  state             = {bundle.state}")
    print(f"  jurisdiction      = {bundle.jurisdiction.get('code')} "
          f"({bundle.jurisdiction.get('name')})")
    print(f"  case_category     = {bundle.case_category.get('code')} "
          f"({bundle.case_category.get('name')})")
    print(f"  case_type         = {bundle.case_type.get('code')} "
          f"({bundle.case_type.get('name')})")
    print(f"  filer_types       = "
          f"{[(r.get('code'), r.get('name')) for r in bundle.filer_types]}")
    print(f"  filing_type_opts  = "
          f"{[(r.get('code'), r.get('name')) for r in bundle.filing_type_options]}")
    print(f"  party_types       = "
          f"{[(r.get('code'), r.get('name'), r.get('is_required')) for r in bundle.party_types]}")
    print(f"  filing_codes ({len(bundle.filing_codes)} total):")
    for row in bundle.filing_codes[:20]:
        print(f"    - {row.get('code')} :: {row.get('name')}")
    if len(bundle.filing_codes) > 20:
        print(f"    ... ({len(bundle.filing_codes) - 20} more) ...")
    print("  document_types_by_filing_code:")
    for code, rows in bundle.document_types_by_filing_code.items():
        pretty = [(r.get("code"), r.get("name")) for r in rows]
        print(f"    {code}: {pretty}")
    print("=" * 72)


# ---------------------------------------------------------------------------
# main


async def run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    auth_token = args.auth_token or settings.USLEGALPRO_AUTH_TOKEN or ""
    if not auth_token:
        logger.error(
            "No auth token: set USLEGALPRO_AUTH_TOKEN in .env or pass --auth-token"
        )
        return 2
    client_token = (
        args.client_token
        or client_token_from_auth_token(auth_token)
        or settings.USLEGALPRO_CLIENT_TOKEN
        or ""
    )
    if not client_token:
        logger.error("No client token available (auth token has no embedded token)")
        return 2

    api_client = USLegalProApiClient(
        auth_token=auth_token,
        client_token=client_token,
    )
    codes_service = USLegalProCodesService(api_client=api_client)

    state = args.state.strip().lower()
    jurisdiction = args.jurisdiction.strip()
    category = args.case_category.strip()
    case_type = args.case_type.strip()

    # 1-3. Confirm jurisdiction / category / case type exist in the live API.
    print(f"[1/8] GET /v2/{state}/code/jurisdiction_codes ...")
    jurisdictions = await codes_service.get_jurisdictions(state)
    if not any(str(row.get("code")) == jurisdiction for row in jurisdictions):
        logger.error(
            "jurisdiction %s not present in state %s (%d returned)",
            jurisdiction, state, len(jurisdictions),
        )
        return 3
    print(f"      found jurisdiction {jurisdiction}")

    # 4. Walk the full chain with the requested filing codes.
    filing_specs = [_parse_filing_spec(spec) for spec in (args.filing or [])]
    if not filing_specs:
        # Fall back to the sample from the guide so the script is runnable
        # even without arguments.
        filing_specs = [
            {
                "code": "209523",
                "doc_type": "53689",
                "file": "https://example.test/documents/complaint.pdf",
                "file_name": "complaint.pdf",
                "description": "Petition",
                "size": 123261,
                "associated_parties": [],
            }
        ]
    filing_codes = [_f["code"] for _f in filing_specs if _f.get("code")]

    print(f"[2/8] walk_case_type_chain(state={state}, "
          f"jurisdiction={jurisdiction}, category={category}, "
          f"case_type={case_type}, filing_codes={filing_codes}) ...")
    bundle = await codes_service.walk_case_type_chain(
        state,
        jurisdiction,
        category,
        case_type,
        filing_codes=filing_codes,
        fetch_optional_services=args.fetch_optional_services,
    )

    if not bundle.case_type:
        logger.error(
            "walk_case_type_chain could not resolve the case type; check "
            "that jurisdiction/category/case_type belong together"
        )
        return 4

    _print_bundle_summary(bundle)

    # 5. Build a synthetic selections dict and assemble the payload.
    print("[3/8] assembling new-case payload with the live bundle ...")
    parties = _build_default_parties(state)
    parties = _apply_party_types_to_defaults(parties, bundle.party_types)
    selections: Dict[str, Any] = {
        "state_code": state,
        "jurisdiction_code": jurisdiction,
        "case_category_code": category,
        "case_type_code": case_type,
        "court_payment_account_id": args.payment_account_id
        or "REPLACE_WITH_PAYMENT_ACCOUNT_ID",
        "provider_fee": args.provider_fee,
        "provider_tax": args.provider_tax,
        "efile_filings": filing_specs,
        "efile_case_parties": parties,
        "filing_party_id": parties[0]["id"] if parties else "",
        "filer_type": args.filer_type or "",
        "filing_type": args.filing_type or "",
    }
    reference_id = args.reference_id or draft_reference_id()
    payload = {
        "data": assemble_new_case_efile_data_live(
            selections=selections,
            bundle=bundle,
            collected_answers={},
            generated_documents=[],
            reference_id=reference_id,
        )
    }
    print("[4/8] payload:")
    print(_dumps(payload))

    # 6. Validate.
    print("[5/8] validating payload ...")
    result = validate_new_case_payload(
        payload, bundle, strict=False, route_state=state
    )
    print(result.format())
    if not result.is_valid:
        print("[6/8] payload FAILED validation; not submitting.")
        return 1
    print("[6/8] payload PASSED validation.")

    # 7. Optional submit.
    if not args.submit:
        print("[7/8] --submit not passed; skipping POST /efile.")
        print("[8/8] done (dry run).")
        return 0

    print(f"[7/8] POST /v2/{state}/efile ...")
    efile_service = USLegalProEFileService(codes_service=codes_service)
    # Use the API client we already built for the walk (same tokens).
    envelope_response = await api_client.submit_efile(state, payload)
    print("[8/8] envelope response:")
    print(_dumps(envelope_response))
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--state", required=True, help="Two-letter state code, e.g. tx")
    p.add_argument("--jurisdiction", required=True, help="e.g. harris:dc")
    p.add_argument("--case-category", required=True, help="Category code, e.g. 131370")
    p.add_argument("--case-type", required=True, help="Case type code, e.g. 209421")
    p.add_argument(
        "--filing",
        action="append",
        help="Repeatable. key=value pairs separated by commas; keys: "
             "code, doc_type, file, file_name, description, size, id.",
    )
    p.add_argument("--payment-account-id", default="", help="Court payment account id")
    p.add_argument("--filer-type", default="", help="Optional filer_type override")
    p.add_argument("--filing-type", default="", help="Optional filing_type override")
    p.add_argument("--provider-fee", default="", help="e.g. 2.99")
    p.add_argument("--provider-tax", default="", help="e.g. 0.25")
    p.add_argument("--reference-id", default="", help="Override the generated reference id")
    p.add_argument(
        "--fetch-optional-services",
        action="store_true",
        help="Also fetch optional_service_codes for every filing code",
    )
    p.add_argument(
        "--auth-token",
        default="",
        help="US Legal Pro auth token; overrides USLEGALPRO_AUTH_TOKEN in .env",
    )
    p.add_argument(
        "--client-token",
        default="",
        help="US Legal Pro client token; overrides the token embedded in auth",
    )
    p.add_argument(
        "--submit",
        action="store_true",
        help="POST the payload to /v2/{state}/efile after successful validation",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except EFilePayloadValidationError as exc:
        print("EFilePayloadValidationError:", file=sys.stderr)
        for err in exc.errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
