"""English-only language policy for filing chat input and output."""

from __future__ import annotations

import re
import unicodedata

ENGLISH_ONLY_REJECTION_MESSAGE = (
    "This service accepts English only. Please continue in English."
)

ENGLISH_ONLY_OUTPUT_FALLBACK = (
    "I can only reply in English. Please continue in English."
)

ENGLISH_ONLY_PROMPT_BLOCK = """## Language
All user-facing replies MUST be in English only. Never reply in another language.
If the user writes in another language, tell them in English that only English is supported."""

_SKIP_PREFIXES = (
    "[session_start]",
    "[workflow_start]",
    "[document_offer]",
    "[upload]",
)

# Scripts that are not used to write English
_NON_LATIN_LETTER_RE = re.compile(
    "["
    "\u0400-\u04FF"  # Cyrillic
    "\u0500-\u052F"
    "\u0590-\u05FF"  # Hebrew
    "\u0600-\u06FF"  # Arabic
    "\u0750-\u077F"
    "\u0900-\u097F"  # Devanagari
    "\u0980-\u09FF"  # Bengali
    "\u0A00-\u0A7F"  # Gurmukhi
    "\u0A80-\u0AFF"  # Gujarati
    "\u0B00-\u0B7F"  # Oriya
    "\u0B80-\u0BFF"  # Tamil
    "\u0C00-\u0C7F"  # Telugu
    "\u0C80-\u0CFF"  # Kannada
    "\u0D00-\u0D7F"  # Malayalam
    "\u0D80-\u0DFF"  # Sinhala
    "\u0E00-\u0E7F"  # Thai
    "\u0E80-\u0EFF"  # Lao
    "\u1000-\u109F"  # Myanmar
    "\u1100-\u11FF"  # Hangul Jamo
    "\u3040-\u30FF"  # Japanese kana
    "\u3130-\u318F"
    "\u3400-\u4DBF"
    "\u4E00-\u9FFF"  # CJK
    "\uAC00-\uD7AF"  # Hangul syllables
    "\uFF66-\uFF9D"
    "]"
)

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ']+")
_NON_ENGLISH_PUNCT_RE = re.compile(r"[¿¡]")

# Distinctive non-English tokens (avoid English words and US place articles)
_NON_ENGLISH_WORDS = frozenset(
    {
        "hola",
        "gracias",
        "quiero",
        "necesito",
        "puedo",
        "estoy",
        "tengo",
        "usted",
        "ustedes",
        "nosotros",
        "nosotras",
        "buenos",
        "buenas",
        "tardes",
        "noches",
        "adios",
        "adiós",
        "porfa",
        "divorcio",
        "archivo",
        "ayuda",
        "presentar",
        "demanda",
        "bonjour",
        "merci",
        "voudrais",
        "besoin",
        "hallo",
        "danke",
        "bitte",
        "möchte",
        "moechte",
        "scheidung",
        "olá",
        "ola",
        "obrigado",
        "obrigada",
        "preciso",
        "quero",
        "ciao",
        "grazie",
        "vorrei",
        "prego",
        "namaste",
        "dhanyavaad",
        "dónde",
        "donde",
        "qué",
        "cómo",
        "como",
        "porqué",
        "porque",
    }
)

_STRONG_GREETINGS = frozenset(
    {
        "hola",
        "bonjour",
        "hallo",
        "olá",
        "ola",
        "ciao",
        "namaste",
        "gracias",
        "merci",
        "danke",
        "grazie",
        "obrigado",
        "obrigada",
    }
)

_NON_ENGLISH_PHRASES = (
    "por favor",
    "buenos dias",
    "buenos días",
    "buenas tardes",
    "buenas noches",
    "s'il vous plait",
    "s'il vous plaît",
    "je voudrais",
    "je suis",
    "ich möchte",
    "necesito ayuda",
    "quiero presentar",
    "quiero archivar",
    "quiero divorciarme",
)


def _fold(word: str) -> str:
    normalized = unicodedata.normalize("NFKD", word or "")
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return stripped.lower()


def _is_internal_message(text: str) -> bool:
    lowered = text.strip().lower()
    return any(lowered.startswith(prefix) for prefix in _SKIP_PREFIXES)


def is_english_text(text: str) -> bool:
    """True when text is empty, internal, or acceptable English (or codes/names)."""
    raw = str(text or "").strip()
    if not raw or _is_internal_message(raw):
        return True
    if _NON_LATIN_LETTER_RE.search(raw) or _NON_ENGLISH_PUNCT_RE.search(raw):
        return False
    lowered = raw.lower()
    if any(phrase in lowered for phrase in _NON_ENGLISH_PHRASES):
        return False
    words = [_fold(match.group(0)) for match in _WORD_RE.finditer(raw)]
    words = [word for word in words if word]
    if not words:
        return True
    foreign = [word for word in words if word in _NON_ENGLISH_WORDS]
    if not foreign:
        return True
    if len(words) <= 3:
        return False
    if len(foreign) >= 2:
        return False
    return foreign[0] not in _STRONG_GREETINGS


def english_or_fallback(text: str, *, fallback: str = ENGLISH_ONLY_OUTPUT_FALLBACK) -> str:
    """Return text if it is English; otherwise a fixed English fallback."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return cleaned
    if is_english_text(cleaned):
        return cleaned
    return fallback
