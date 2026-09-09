"""Prompt for mapping workflow answers onto template field_mapping targets."""

FIELD_MAPPING_PROMPT = """You map workflow answers onto template output fields.

## Field mapping configuration
Each entry is TARGET=SOURCE or TARGET=join(" ", FIELD1, FIELD2, ...).
Entries are separated by | (pipe).

{field_mapping}

## Sample generate_documents payload (structure and value types)
Use this as the authoritative example for how each form_data value must look.
The top-level id, state, jurisdiction, and version are fixed from this sample —
only map form_data values from user answers.
Match the same value types: "yes"/"no" for booleans, numeric codes where shown,
MM/DD/YYYY for dates, county names (not yes/no), and plain text where shown.
Do not replace boolean or coded values with full sentences from user answers.

{sample_input_json}

## Workflow questions
{workflow_questions_json}

## User answers collected during chat
{collected_answers_json}

## Initial filled form JSON (PDF field labels to values)
{filled_json}

Rules:
- Return one JSON object with a "fields" map.
- Every key in fields MUST be the TARGET name from field mapping (left side of =), copied exactly including leading underscores.
- For direct mappings (TARGET=SOURCE), copy the value from user answers or filled JSON that matches SOURCE (field_name, pdf_field, or label).
- For join(" ", A, B, C) mappings, combine the referenced source values with a single space. Omit empty parts; trim the result.
- Normalize answers to match the sample payload types before returning (yes/no, numeric codes, dates).
- Use ONLY values from user answers and filled JSON. Do not invent facts.
- If a source value is missing, set that TARGET to "".
- Include every TARGET field listed in the field mapping configuration.

Return JSON only:
{{
  "fields": {{
    "_FULL_NAME": "Jane Q Public"
  }}
}}
"""
