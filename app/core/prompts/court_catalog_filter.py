"""Prompts for LLM-based case intent extraction and court catalog filtering."""

CASE_INTENT_EXTRACTION_PROMPT = """You help a layperson start a court filing using natural language.

Extract what kind of case they want to file from their message.

## User message
{user_message}

## Selections already made
{selections_json}

## Conversation history (recent)
{history_text}

Return JSON only:
{{
  "case_topic": "short topic label, e.g. divorce, eviction, small claims, child custody",
  "summary": "one sentence describing the filing the user wants",
  "search_phrases": ["2-5 phrases that would appear in official court case type names"]
}}

Rules:
- Understand plain English, slang, and incomplete sentences.
- The user message is English only. Extract the topic in English.
- Ignore filler words like "I need", "help me", "file a case for".
- If the user only states intent to file a new case without a case type, set case_topic to "".
- search_phrases must be useful for matching official court catalog case type names.
- Do not invent facts not stated by the user.
"""

COURT_MATCH_PROMPT = """You help a layperson find which courts can handle their filing.

## User filing request
Topic: {case_topic}
Summary: {summary}
Search phrases: {search_phrases}

## Courts in {state_code}
Each line is: court_code | court_name | case_types_offered

{court_lines}

Return JSON only:
{{
  "matched_court_codes": ["court_code1", "court_code2"],
  "reason": "brief explanation"
}}

Rules:
- Include a court ONLY if it offers at least one case type that fits the user's request.
- Use the case_types_offered list — do not invent case types.
- Exclude courts whose only related types contradict the request (e.g. "No Divorce" when user wants divorce).
- Return court_code values exactly as shown before the first pipe.
- Return an empty matched_court_codes list when no court fits this batch.
"""
