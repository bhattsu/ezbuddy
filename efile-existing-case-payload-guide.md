# Map a case-detail response to an existing-case `/efile` payload

This document shows which response node supplies each field in the existing-case payload.

## Main mapping

| Payload field | Source node | Value to copy |
| --- | --- | --- |
| `data.case_tracking_id` | Case-detail response: `item.case_tracking_id` | Copy the complete string exactly. |
| `data.filing_party_id` | Case-detail response: selected `item.case_parties[n].id` | Copy the `id` of the party for whom the filing is submitted. |
| `data.filer_type` | Call `item.link.filer_type_codes.link`, then select `items[n].code` from that response | Copy the selected filer type's `code`. |
| `data.filings[i].code` | Call `item.link.filing_codes.link`, then select `items[n].code` from that response | Copy the code matching the document being filed. |
| `data.filings[i].doc_type` | From the selected filing-code item, call `link.document_type_codes.link`; then select `items[n].code` | Copy the selected document type's `code`. |
| `data.filings[i].optional_services[j].code` | From the selected filing-code item, call `link.optional_service_codes.link`; then select `items[n].code` | Copy only a selected optional service's `code`. |
| `data.filing_type` | Call `item.link.filing_type.link` and select an allowed value | Use `EFile`, `EFileAndServe`, or `Serve`. |
| `data.payment_account_id` | Payment-account response: selected `items[n].id` | Copy the payment account's `id`. |

## Mapping flow

```text
case-detail.item.case_tracking_id
    -> data.case_tracking_id

case-detail.item.case_parties[selected].id
    -> data.filing_party_id

case-detail.item.link.filer_type_codes.link
    -> call link
    -> response.items[selected].code
    -> data.filer_type

case-detail.item.link.filing_codes.link
    -> call link
    -> response.items[selected].code
    -> data.filings[i].code

selected filing-code item.link.document_type_codes.link
    -> call link
    -> response.items[selected].code
    -> data.filings[i].doc_type

selected filing-code item.link.optional_service_codes.link
    -> call link
    -> response.items[selected].code
    -> data.filings[i].optional_services[j].code
```

## Direct values from the supplied case-detail response

### `data.case_tracking_id`

Source:

```text
item.case_tracking_id
```

Mapping:

```json
"case_tracking_id": "tyler_hidalgo:dc389~320a32c7-fca6-4230-954a-ba38b5b1fadb~CT"
```

Do not use `item.case_number` (`F-129-10-H`) as `case_tracking_id`.

### `data.filing_party_id`

Source:

```text
item.case_parties[n].id
```

Choose the party on whose behalf the document is filed. For example, if the first returned party is the filing party:

```json
"filing_party_id": "c2540bea-f147-4b68-a18d-efc734d7a8b6"
```

Do not use these nodes for `filing_party_id`:

- `item.case_parties[n].type` — this is a party-type code.
- `item.case_parties[n].lead_attorney` — this is an attorney ID.
- `item.case_parties[n].first_name` or `last_name` — these are display data.

## Codes obtained by following case-detail links

The case-detail response supplies lookup links, not the final selected codes.

### `data.filer_type`

1. Read the URL from `item.link.filer_type_codes.link`.
2. Call it using the header described by `item.link.filer_type_codes.header`.
3. Choose the filer type matching the submitter.
4. Copy the selected response item's `code` to `data.filer_type`.

```text
filer_type_codes response.items[n].code -> data.filer_type
```

Do not copy the item's `name` or the link's `request_id`.

### `data.filings[i].code`

1. Read the URL from `item.link.filing_codes.link`.
2. Call it using the supplied header.
3. Choose the filing whose `name` matches the actual document, such as the correct type of affidavit.
4. Copy that item's `code`.

```text
filing_codes response.items[n].code -> data.filings[i].code
```

The link already carries this case's jurisdiction, category, case type, and subsequent-filing context. Do not replace its `request_id` or use a filing code from another case branch.

### `data.filings[i].doc_type`

The `document_type_codes` link is returned on the selected filing-code item, not directly on the case-detail response.

```text
selected filing_codes item.link.document_type_codes.link
    -> document_type_codes response.items[n].code
    -> data.filings[i].doc_type
```

Select a lead/main document type for the primary document and an attachment/supporting type only for an attachment.

### Optional service code

If an optional service is needed:

```text
selected filing_codes item.link.optional_service_codes.link
    -> optional_service_codes response.items[n].code
    -> data.filings[i].optional_services[j].code
```

Do not add an optional service merely because it is returned.

## Case-detail nodes not copied into this payload

These values describe the existing case or help build code links. They are not fields in the sample existing-case payload:

| Case-detail node | Purpose |
| --- | --- |
| `item.jurisdiction` | Existing court/location code. It is already represented by `case_tracking_id` and the returned links. |
| `item.case_category` | Existing case category. Do not send it as a new-case selection. |
| `item.case_type` | Existing case type. Do not send it as a new-case selection. |
| `item.case_number` | Human-facing court case number; it is not `case_tracking_id`. |
| `item.case_parties[n].type` | Existing party-role code; it is not `filing_party_id`. |
| `item.link.party_type_codes` | Used only if a workflow needs valid party-role codes. It does not populate a field in the shown payload. |
| `item.link.location_code` | Returns location details; it does not replace `case_tracking_id`. |
| `item.link.case_subtype_codes` | Used only when the filing workflow requires a case subtype. |
| `item.link.name_suffix_codes` | Used only for a party name suffix. |
| `item.link.disclaimer_requirement_codes` | Used only when the filing requires a disclaimer response. |
| `item.link.countries` and `item.link.states` | Used when a workflow asks for address codes. |
| `item.link.case_service_contacts` | Returns service contacts used for `EFileAndServe` or `Serve`; it is not a filing code. |

## Other fields supplied by the developer

| Payload field | Value |
| --- | --- |
| `data.reference_id` | A new unique caller-generated value. |
| `data.filings[i].file_name` | Actual document filename. |
| `data.filings[i].description` | Human-readable document description. |
| `data.filings[i].file` | URL from which the filing service can download the document. |
| `data.filings[i].size` | File size in bytes, when included. |
| `data.filings[i].id` | Caller-generated filing ID, when included. |
| `data.filings[i].associated_parties` | Existing `item.case_parties[n].id` values, when required. |

## Payload template

```json
{
  "data": {
    "reference_id": "<new unique value>",
    "case_tracking_id": "<case-detail item.case_tracking_id>",
    "payment_account_id": "<payment account items[n].id>",
    "filing_party_id": "<case-detail item.case_parties[n].id>",
    "filing_type": "<allowed filing_type value>",
    "filer_type": "<filer_type_codes response.items[n].code>",
    "filings": [
      {
        "code": "<filing_codes response.items[n].code>",
        "file_name": "Affidavit.pdf",
        "description": "Affidavit",
        "doc_type": "<document_type_codes response.items[n].code>",
        "file": "https://example.test/Affidavit.pdf"
      }
    ]
  }
}
```

## Developer checks

- Always copy `code`, never `name`, into a code-backed payload field.
- Keep numeric-looking codes as JSON strings.
- Preserve each returned `request_id`; it contains the selected case context.
- Use the existing party's `id` for `filing_party_id`, not the party's `type`.
- Use `EFile`, `EFileAndServe`, or `Serve`; `EFileAnd` is not valid.
- Obtain document types and optional services from the selected filing-code item's links.
