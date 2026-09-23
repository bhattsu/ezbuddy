"""Prompt for mapping a user correction to exactly one workflow field."""

WORKFLOW_FIELD_CORRECTION_PROMPT = """You map a user's correction message to exactly ONE form field.

## Open form fields (field_name → question)
{fields_json}

## Current values (field_name → value)
{answers_json}

## User correction message
{user_message}

Rules:
- Return exactly one field_name in field_names array (length 1) when you can identify the target field.
- Put the new value in "value".
- Match by question meaning, not loose word overlap. Do NOT update multiple fields.
- "Change my name" / "change me name" / "rename me" → only the filing user's full legal name field (Plaintiff/Petitioner FULL_NAME, not Defendant, not SSN, not zip, not name change yes/no).
- "Change defendant name" → only Defendant/Respondent full legal name field.
- Only pick fields whose question text clearly matches what the user wants to change (phone → phone field, email → email, address → address).
- If the user did not specify which field and you cannot pick one field with confidence, set intent to unclear and field_names to [].

Respond with JSON only:
{{"intent": "update" or "unclear", "field_names": ["FIELD_NAME"], "value": "new value or null"}}
"""
