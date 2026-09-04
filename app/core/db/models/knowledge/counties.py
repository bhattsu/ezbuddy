from __future__ import annotations

from typing import TYPE_CHECKING, Optional
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint, text as sql_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import Computed

from app.core.db.base import Base


class KnowledgeCounty(Base):
    __tablename__ = "counties"
    __table_args__ = {"schema": "knowledge"}

    county_id: Mapped[str] = mapped_column(Text, primary_key=True)
    jurisdiction_id: Mapped[str] = mapped_column(Text, ForeignKey("knowledge.jurisdictions.jurisdiction_id"), nullable=False)
    county_name: Mapped[str] = mapped_column(Text, nullable=False)
    effective_date: Mapped[Optional[date]] = mapped_column(Date)
    last_verified: Mapped[Optional[date]] = mapped_column(Date)
