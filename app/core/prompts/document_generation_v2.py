"""Prompt for document generation 2.0: map user answers onto exact PDF field keys."""

DOCUMENT_GENERATION_V2_PROMPT = """You fill a US court form as JSON.

Use ONLY the extracted PDF text and field list as the source of JSON keys.
Use ONLY the user answers as the source of JSON values.

## Extracted PDF text
{extracted_text}

## Extracted PDF fields (these labels are the exact JSON keys)
{extracted_fields_json}

## User answers
{answers_json}

Rules:
- Return one JSON object with a "fields" map.
- Every key in fields MUST be copied EXACTLY from the extracted PDF field labels.
  Prefer the "field" value. If field is missing, use the question text as the key.
- Do not rename, translate, slug, or invent keys.
- Include every extracted field, even when the answer is blank (use "").
- Values MUST come from the matching user answer. Copy the user's value; do not invent facts.
- Match answers by field label, question text, or similar wording. Numbered replies map in order when needed.
- If there is no user answer for a field, set the value to "".
- Do not include HTML, markdown, or extra commentary.

Return JSON only:
{{
  "fields": {{
    "Cause Number": "20250622001"
  }}
}}
"""
