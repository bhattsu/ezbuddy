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


from pgvector.sqlalchemy import Vector

class KbVector(Base):
    __tablename__ = "kb_vectors"
    __table_args__ = (
        Index("idx_kb_vectors_filters", "matter_type", "jurisdiction_id", "county_id", "content_type"),
        {"schema": "knowledge"},
    )

    content_id: Mapped[str] = mapped_column(Text, primary_key=True)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    matter_type: Mapped[str] = mapped_column(Text, ForeignKey("knowledge.matter_types.matter_type_id"), nullable=False)
    jurisdiction_id: Mapped[str] = mapped_column(Text, ForeignKey("knowledge.jurisdictions.jurisdiction_id"), nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    county_id: Mapped[Optional[str]] = mapped_column(Text, ForeignKey("knowledge.counties.county_id"))
    case_variant: Mapped[Optional[str]] = mapped_column(Text)
    scope: Mapped[Optional[str]] = mapped_column(Text)
    topics: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    requires_local_verification: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=sql_text("false"))
    chunk_text: Mapped[str] = mapped_column("text", Text, nullable=False)
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1024))
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    last_verified: Mapped[Optional[date]] = mapped_column(Date)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=sql_text("now()"))
    text_search: Mapped[Optional[str]] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', text)", persisted=True),
    )
