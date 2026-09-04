"""Prompt for deduplicating and clustering workflow questions from DB + document sources."""

WORKFLOW_QUESTION_CONSOLIDATION_PROMPT = """You are a US legal filing assistant.

Combine workflow questions from two sources into a shorter list the user can answer without repetition:
1. Database workflow questions (configured for the case type)
2. Document form fields (extracted from the court PDF template)

## Case context
{selections_json}

## Database workflow questions
{db_questions_json}

## Document-analyzed form questions
{document_questions_json}

## Task
1. Remove semantic duplicates — same fact asked twice (e.g. petitioner name in DB and on the form).
2. Cluster related fields that belong together (e.g. petitioner name + address, spouse details, child info).
3. Produce one clear natural-language question per cluster the user can answer in one turn when possible.
4. Keep distinct legal facts separate (do not merge unrelated fields).
5. Prefer the document form field_name as canonical when a DB question maps to the same PDF field concept.
6. Preserve required vs optional: a cluster is required if any member field is required.

Rules:
- Every input field_name must appear exactly once: either as canonical_field_name or in duplicate_fields of some cluster.
- duplicate_fields are exact semantic duplicates whose answer will be copied from the canonical field.
- cluster_fields lists every field_name covered by the cluster (including canonical and duplicates).
- Do not invent new field names.
- Order clusters logically for filing (parties, court/case, facts, relief, signatures last).
- Skip clusters whose facts are already fully answered in case context when obvious (e.g. court already selected).

Respond with JSON only (no markdown fences):
{{
  "clusters": [
    {{
      "cluster_id": "short_snake_case_id",
      "question": "One natural question for the user",
      "canonical_field_name": "primary_field_name",
      "cluster_fields": ["field_a", "field_b"],
      "duplicate_fields": ["field_b"],
      "required": true,
      "sort_order": 1
    }}
  ],
  "removed_duplicates": [
    {{"removed_field": "field_b", "kept_field": "field_a", "reason": "same as petitioner name"}}
  ],
  "summary": "Brief note on how many questions were merged"
}}
"""

WORKFLOW_CONSOLIDATION_OUTPUT_KEYS = (
    "clusters",
    "removed_duplicates",
    "summary",
)
