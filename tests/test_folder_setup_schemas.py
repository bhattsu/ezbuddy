"""Folder setup request validation."""

import pytest

from app.api.schemas.folder_setup import (
    CreateJurisdictionFolderRequest,
    CreateStateFolderRequest,
)


def test_state_request_normalizes_code():
    body = CreateStateFolderRequest(code=" tx ", name="Texas")
    assert body.code == "TX"
    assert body.name == "Texas"


def test_jurisdiction_request_rejects_path_separators():
    with pytest.raises(ValueError):
        CreateJurisdictionFolderRequest(state="TX", jurisdiction_name="travis/civil")
