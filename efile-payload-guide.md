# Tyler `/efile` payload and filing-code guide

This guide explains how to build the initial-case Tyler payload. The documentation examples use Texas (`/v2/tx`) and `is_initial=true`, but filing codes are court- and case-specific. Always load the current codes for the selected state and jurisdiction instead of hard-coding the example values.

## The most important rule

A code lookup returns a response containing an `items` array. Use the selected item's **`code`** as the value in the `/efile` payload. Use `name` only as a display label in a user interface.

For example, given a jurisdiction result like this:

```json
{
  "code": "harris:dc",
  "name": "Harris County District Court",
  "link": {
    "case_category_codes": {
      "link": "https://api-stage.uslegalpro.com/v2/tx/code/case_category_codes?request_id=...",
      "method": "GET",
      "header": {
        "clienttoken": "..."
      }
    }
  }
}
```

the payload must contain:

```json
"jurisdiction": "harris:dc"
```

Do not send the `name`, the entire result object, or the `request_id` as the field value.

## Code-selection order

Tyler codes form a dependency chain. A filing code that is valid for one court, category, or case type may be invalid for another.

```text
jurisdiction
  -> case category
     -> case type
        -> party types
        -> filer type
        -> filing code
           -> document type
           -> optional services (when used)
```

The preferred workflow is:

1. Call `GET /v2/{state}/code/jurisdiction_codes?is_initial=true&court_system=tyler`.
2. Select a jurisdiction and copy `items[n].code` to `data.jurisdiction`.
3. Follow the URL in that item's `link.case_category_codes.link`.
4. Select a category, copy its `code` to `data.case_category`, and follow the URL in its `link.case_type_codes.link`.
5. Select a case type and copy its `code` to `data.case_type`.
6. Follow the selected case type's links to obtain party types, filer types, and filing codes.
7. For each selected filing, follow its links to obtain valid document types and optional services.

Every filing-code request needs the `clienttoken` header. Links returned by the API contain an encoded `request_id` that preserves the earlier selections. Treat it as opaque: use the returned URL without decoding or modifying it.

## Payload field-to-code mapping

| `/efile` payload field | Value to send | Source | Dependencies / notes |
| --- | --- | --- | --- |
| `data.jurisdiction` | Selected `items[n].code` | `GET /v2/{state}/code/jurisdiction_codes?is_initial=true&court_system=tyler` | This is also called the location code by downstream endpoints. In the example it is `harris:dc`. |
| `data.case_category` | Selected category `code` | Follow the jurisdiction item's `link.case_category_codes.link`, or call `case_category_codes` with `location_code` | Must belong to the selected jurisdiction. Example: `131370`. |
| `data.case_type` | Selected case-type `code` | Follow the category item's `link.case_type_codes.link`, or call `case_type_codes` with jurisdiction, category, and `is_initial=true` | Must belong to the selected jurisdiction and category. Example: `209421`. |
| `data.filer_type` | Selected filer-type `code` | Follow the selected case type's `link.filer_type_codes.link`, or call `filer_type_codes` with `location_code` | Texas Tyler field. Example: `54325`. |
| `data.case_parties[i].type` | Selected party-type `code` | Follow the selected case type's `link.party_type_codes.link`, or call `party_type_codes` with `location_code`, `case_type_code`, and normally `is_required=Both` | Create the required party roles returned for the selected case type. Examples: `53024`, `271011`. |
| `data.filings[i].code` | Selected filing `code` | Follow the selected case type's `link.filing_codes.link`, or call `filing_codes` with jurisdiction, category, case type, and `is_initial=true` | One object is needed for each document being filed. Example: `209523`. |
| `data.filings[i].doc_type` | Selected document-type `code` | Follow the selected filing item's `link.document_type_codes.link`, or call `document_type_codes` with `location_code` and `filing_code` | The valid document types depend on both jurisdiction and filing code. Example: `53689`. |
| `data.filings[i].optional_services[j].code` (if present) | Selected optional-service `code` | Follow the filing item's `link.optional_service_codes.link`, or call `optional_service_codes` with `location_code` and `filing_code` | Not present in the sample payload. Include only selected/required services. |

## Fields that are not filing codes

These fields must not be populated from an unrelated `code` result:

| Payload field | What it contains |
| --- | --- |
| `data.reference_id` | A caller-generated identifier that must be unique for each filing submission. Generate a new value when retrying as a new submission. |
| `data.payment_account_id` | The payment account's `id` from `GET /v2/{state}/payment_accounts`; it is not a court filing code. |
| `data.filing_type` | Filing action, such as `EFile` or `EFileAndServe`. Available choices can be obtained from the URL in the selected case type's `link.filing_type.link`. This is different from `filings[i].code`. |
| `data.filing_state` | Lowercase state abbreviation matching the route, for example `tx` when posting to `/v2/tx/efile`. |
| `data.provider_fee` | Provider fee amount, represented as a decimal string. This is not supplied by the court-code endpoints. |
| `data.provider_tax` | Tax on the provider fee, represented as a decimal string. This is not a filing code. |
| `data.filing_party_id` | The `id` of one object in this payload's `case_parties` array. It is a local cross-reference, not a party-type code. |
| `data.case_parties[i].id` | A caller-created, unique ID for that party within the payload, such as `Party_8168279`. |
| `data.case_parties[i].country` | Country code, such as `US`. |
| `data.case_parties[i].state` | State code, such as `TX`; do not put the filing jurisdiction here. |
| `data.case_parties[i].first_name`, `last_name`, `business_name` | Party identity. Use person names when `is_business=false`; use `business_name` when filing for an organization. |
| `data.case_parties[i].is_business` | JSON boolean (`true` or `false`), not a quoted code. |
| `data.case_parties[i].lead_attorney` | Attorney identifier applicable to that party, or the supported self-represented value such as `PRO SE`. It is not a party-type code. |
| `data.case_parties[i].additional_attorneys` | Array of additional attorney identifiers; use `[]` when none apply. |
| `data.filings[i].id` | A caller-created, unique ID for the filing/document within the payload. |
| `data.filings[i].file` | A URL accessible to the filing service from which the PDF can be downloaded. |
| `data.filings[i].file_name` | File name, normally ending in `.pdf`. |
| `data.filings[i].description` | Human-readable document description; it is not the filing-code display name unless the application intentionally uses that text. |
| `data.filings[i].size` | File size in bytes. It should match the document at `file`. |
| `data.filings[i].associated_parties` | Array of `case_parties[].id` values associated with this filing, not party-type codes. Use `[]` when no association is required. |

## Annotated sample payload

The comments below are explanatory; remove them before sending JSON.

```jsonc
{
  "data": {
    "filer_type": "54325",             // filer_type_codes[].code
    "reference_id": "DRAFT-2026-1003458", // unique caller value
    "jurisdiction": "harris:dc",       // jurisdiction_codes[].code
    "payment_account_id": "013af35d-66f2-4aa8-85bc-d3aecf8c4443",
    "filings": [
      {
        "code": "209523",              // filing_codes[].code
        "file_name": "complaint.pdf",
        "description": "Petition",
        "doc_type": "53689",           // document_type_codes[].code
        "file": "https://example.test/documents/complaint.pdf",
        "size": 123261,
        "associated_parties": [],       // case_parties[].id values, if required
        "id": "Filing_1"
      }
    ],
    "case_parties": [
      {
        "country": "US",
        "city": "Houston",
        "type": "53024",               // party_type_codes[].code
        "zip_code": "77002",
        "address_line_1": "1200 Baker Street",
        "id": "Party_1",
        "state": "TX",
        "first_name": "JANE",
        "is_business": false,
        "lead_attorney": "PRO SE",
        "last_name": "DOE",
        "additional_attorneys": []
      },
      {
        "country": "US",
        "type": "271011",              // party_type_codes[].code
        "id": "Party_2",
        "first_name": "JOHN",
        "is_business": false,
        "last_name": "DOE",
        "additional_attorneys": []
      }
    ],
    "provider_tax": "0.25",
    "filing_type": "EFileAndServe",
    "filing_state": "tx",
    "case_type": "209421",             // case_type_codes[].code
    "provider_fee": "2.99",
    "case_category": "131370",         // case_category_codes[].code
    "filing_party_id": "Party_1"        // must match a case_parties[].id
  }
}
```

The numeric-looking court codes are strings. Keep the quotation marks so leading zeros, if any, are preserved.

## Validation checklist before submission

- The route state, `filing_state`, jurisdiction, and all selected codes belong to the same court system and state.
- `jurisdiction`, `case_category`, `case_type`, `filer_type`, every party `type`, every filing `code`, and every `doc_type` came from the same dependency chain.
- All required party types returned by the party-type lookup are represented.
- `filing_party_id` exactly matches one `case_parties[].id`.
- Every value in `associated_parties` exactly matches one `case_parties[].id`.
- Every filing has its own unique `id`, accessible file URL, correct file name, and byte size.
- `reference_id` has not already been used for another submission.
- The payment account comes from `/payment_accounts` and is valid for the authenticated filer.
- The POST uses `authtoken`; code lookups use `clienttoken`.

Finally, submit the valid JSON to:

```http
POST {{HOST}}/v2/{{STATE}}/efile
Content-Type: application/json
authtoken: {{authtoken}}
```
