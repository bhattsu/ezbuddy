"""Prompt for classifying free-text e-file confirmation replies."""

EFILE_CONFIRM_INTENT_PROMPT = """The user is reviewing an e-file submission JSON before it is sent to the court.

## User message
{user_message}

Return JSON only:
{{
  "intent": "proceed | cancel | unclear"
}}

Definitions:
- proceed: they want to submit / e-file / go ahead with this request (any natural wording).
- cancel: they want to stop, go back, or not submit this e-file.
- unclear: not about submitting or cancelling (questions about the JSON, unrelated chat, or ambiguous).

Rules:
- "proceed with efiling", "submit it", "looks good send it", "yes" → proceed.
- "no", "cancel", "don't file" → cancel.
- English only.
"""
