"""Map uploaded-document analysis onto workflow question field codes."""

ANALYSIS_PREFILL_PROMPT = """You map extracted court-document fields onto a workflow question schema.

Use only field_name values from workflow_questions. Never invent fields or answers.
Only include a field when the documents clearly contain that value.
If a value is missing, ambiguous, or not in the documents, omit it.

## Workflow questions (target schema)
{workflow_questions_json}

## Already collected answers (do not overwrite these)
{collected_answers_json}

## Combined document analysis
{analyses_json}

Respond with JSON only (no markdown fences):
{{
  "answers_update": {{}}
}}
"""
