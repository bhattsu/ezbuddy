"""Prompt for mapping collected filing data onto a new-case e-file request."""

EFILE_MAPPING_PROMPT = """You build the POST /v2/{{state}}/efile request body for a NEW case.

## Request shape (field names and types)
Use this sample only as the schema. Do not copy its dummy names, addresses,
codes, fees, file URLs, payment ids, or party ids.

{sample_request_json}

## Known filing session values (authoritative codes and payment/document facts)
{known_facts_json}

## Party / filing / document type options from the court catalog
Use these codes when you need type, filer_type, filing code, or doc_type.
{catalog_options_json}

## Workflow questions
{workflow_questions_json}

## User answers collected during chat
{collected_answers_json}

## Mapped form data from the generated court document
{form_data_json}

Rules:
- Return one JSON object with a "data" map that matches the sample keys.
- Fill values only from known facts, catalog options, user answers, and form data.
- Do not invent people, addresses, codes, fees, or URLs.
- Split full names into first_name / last_name when needed.
- Map plaintiff/petitioner and defendant/respondent (and any other parties) into case_parties.
- Use catalog option codes for party type, filing code, doc_type, and filer_type when available.
- filing_party_id must be the id of the filing party in case_parties.
- Keep associated_parties and additional_attorneys as lists.
- is_business must be true or false.
- If a value is unknown, use "" (or [] / false for those types).
- Do not copy sample placeholders, hardcoded codes, or unmatched catalog values.

Return JSON only:
{{
  "data": {{}}
}}
"""

EXISTING_CASE_EFILE_MAPPING_PROMPT = """You build the POST /v2/{{state}}/efile request body for an EXISTING case.

## Request shape (field names and types)
Use this sample only as the schema. Do not copy its dummy codes, names, or URLs.

{sample_request_json}

## Known filing session values (authoritative case, payment, and document facts)
{known_facts_json}

## Filing / document type options and existing-case parties from the court
{catalog_options_json}

## Workflow questions
{workflow_questions_json}

## User answers collected during chat
{collected_answers_json}

## Mapped form data from the generated court document
{form_data_json}

Rules:
- Return one JSON object with a "data" map that matches the sample keys only.
- Fill values only from known facts, catalog options, user answers, and form data.
- Do not invent codes, party ids, fees, or URLs.
- Use the generated document file URL from known facts for filings[0].file.
- Use case_tracking_id, payment_account_id, and filing_party_id from known facts when present.
- Use catalog codes for filing code and doc_type when they match the selected document.
- If a value is unknown, use "". Do not copy sample placeholders or unmatched codes.

Return JSON only:
{{
  "data": {{}}
}}
"""

EFILE_PARTY_NAME_MAPPING_PROMPT = """You fill missing person names on NEW-case e-file parties.

Only map first_name and last_name. Do not change party ids, types, addresses,
attorneys, codes, filings, or any other payload field.

## Parties that still need a person name
{parties_json}

## User answers collected during chat
{collected_answers_json}

## Mapped form data from the generated court document
{form_data_json}

## Workflow questions
{workflow_questions_json}

## Other available session text
{session_hints_json}

Rules:
- Return JSON with a "parties" array. Each item must include the same "id"
  as the input party (and "type" when you know it).
- Fill first_name and last_name only from the provided answers, form data,
  questions, or session text. Split a full name when needed.
- Match names to the party role (petitioner/plaintiff vs respondent/defendant).
- Do not invent people. If a name is not in the data, use "".
- Do not copy sample names such as JANE DOE or JOHN DOE unless those exact
  names appear in the provided data.
- Leave business parties with empty first_name and last_name.

Return JSON only:
{{
  "parties": [
    {{
      "id": "Party_1",
      "type": "",
      "first_name": "",
      "last_name": ""
    }}
  ]
}}
"""
