"""Schemas for template folder setup APIs (S3 + configuration.states)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class CreateStateFolderRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=10, examples=["TX"])
    name: str = Field(..., min_length=1, max_length=100, examples=["Texas"])

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        text = str(value or "").strip().upper()
        if not text:
            raise ValueError("code is required")
        return text

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("name is required")
        return text


class CreateJurisdictionFolderRequest(BaseModel):
    state: str = Field(..., min_length=1, max_length=10, examples=["TX"])
    jurisdiction_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        examples=["Travis"],
        description="Folder name under the state (e.g. Travis → documents-repo/templates/TX/Travis/)",
    )

    @field_validator("state")
    @classmethod
    def normalize_state(cls, value: str) -> str:
        text = str(value or "").strip().upper()
        if not text:
            raise ValueError("state is required")
        return text

    @field_validator("jurisdiction_name")
    @classmethod
    def normalize_jurisdiction(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("jurisdiction_name is required")
        if "/" in text or "\\" in text:
            raise ValueError("jurisdiction_name must not contain path separators")
        return text


class StateFolderResponse(BaseModel):
    id: UUID
    code: str
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    s3_bucket: str
    s3_prefix: str
    s3_marker_key: str
    s3_folder_created: bool
    db_created: bool


class JurisdictionFolderResponse(BaseModel):
    state: str
    jurisdiction_name: str
    s3_bucket: str
    s3_prefix: str
    s3_marker_key: str
    s3_folder_created: bool
    state_found_in_s3: bool
