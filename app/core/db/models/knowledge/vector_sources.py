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


class VectorSource(Base):
    __tablename__ = "vector_sources"
    __table_args__ = {"schema": "knowledge"}

    content_id: Mapped[str] = mapped_column(Text, ForeignKey("knowledge.kb_vectors.content_id"), primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, ForeignKey("knowledge.sources.source_id"), primary_key=True)
