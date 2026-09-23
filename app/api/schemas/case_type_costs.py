"""Schemas for case type filing cost configuration API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class CaseTypeCostUpsertRequest(BaseModel):
    case_type: str = Field(..., min_length=1, max_length=100)
    state_code: Optional[str] = Field(default=None, max_length=10)
    cost: Decimal = Field(..., ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    is_active: bool = True


class CaseTypeCostResponse(BaseModel):
    id: UUID
    case_type: str
    state_code: Optional[str] = None
    cost: Decimal
    currency: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CaseTypeCostListResponse(BaseModel):
    items: list[CaseTypeCostResponse]
    total: int
