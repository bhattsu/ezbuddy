"""Tests for US Legal Pro auth token parsing."""

import pytest

from app.adapters.uslegalpro.tokens import parse_auth_token


def test_parse_auth_token():
    token = "116756a7-75f7-4f06-88e2-7f76eda0e7fa/GENS77/ea73f958-1304-4dcd-8934-6801194e61b9"
    user_id, client_token, session_id = parse_auth_token(token)
    assert user_id == "116756a7-75f7-4f06-88e2-7f76eda0e7fa"
    assert client_token == "GENS77"
    assert session_id == "ea73f958-1304-4dcd-8934-6801194e61b9"


def test_parse_auth_token_invalid():
    with pytest.raises(ValueError):
        parse_auth_token("not-a-valid-token")
