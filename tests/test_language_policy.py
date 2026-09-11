"""English-only language policy for filing chat."""

from app.agents.utils.language_policy import is_english_text


def test_allows_english_filing_request():
    assert is_english_text("I need to file for divorce in Texas")
    assert is_english_text("Harris District Court")
    assert is_english_text("My spouse is José García")


def test_allows_codes_and_internal_tokens():
    assert is_english_text("TX")
    assert is_english_text("20250622001")
    assert is_english_text("[session_start]")
    assert is_english_text("[upload] SmallClaimsPetition.pdf")


def test_rejects_spanish_and_other_latin_languages():
    assert not is_english_text("Hola")
    assert not is_english_text("Quiero presentar una demanda de divorcio")
    assert not is_english_text("Bonjour, je voudrais un divorce")
    assert not is_english_text("¿Puedo archivar un caso?")


def test_rejects_non_latin_scripts():
    assert not is_english_text("我想申请离婚")
    assert not is_english_text("मैं तलाक के लिए आवेदन करना चाहता हूँ")
    assert not is_english_text("Я хочу подать на развод")
