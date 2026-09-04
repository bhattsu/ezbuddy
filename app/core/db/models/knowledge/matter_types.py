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


class MatterType(Base):
    __tablename__ = "matter_types"
    __table_args__ = {"schema": "knowledge"}

    matter_type_id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    required_content_types: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    high_risk_topics: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    default_disclaimer: Mapped[Optional[str]] = mapped_column(Text)
    active: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=sql_text("true"))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=sql_text("now()"))
