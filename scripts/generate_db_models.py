"""Generate app/core/db/models from us_legal_pro_install_all.sql structure. Run: python scripts/generate_db_models.py"""
from __future__ import annotations

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app", "core", "db", "models"))

H = '''from __future__ import annotations

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

'''


def w(rel: str, body: str) -> None:
    path = os.path.join(ROOT, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(H + body)


# --- configuration (11) ---
w("configuration/states.py", '''
class State(Base):
    __tablename__ = "states"
    __table_args__ = {"schema": "configuration"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("true"))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("configuration/counties.py", '''
class County(Base):
    __tablename__ = "counties"
    __table_args__ = (UniqueConstraint("state_id", "name"), {"schema": "configuration"})

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    state_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.states.id", ondelete="RESTRICT"), nullable=False)
    code: Mapped[Optional[str]] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=sql_text("true"))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("configuration/jurisdictions.py", '''
class Jurisdiction(Base):
    __tablename__ = "jurisdictions"
    __table_args__ = (
        CheckConstraint(
            "jurisdiction_type IN ('DISTRICT_COURT','FAMILY_COURT','SMALL_CLAIMS_COURT',"
            "'PROBATE_COURT','COUNTY_COURT','MUNICIPAL_COURT','APPELLATE_COURT','OTHER')",
            name="jurisdictions_jurisdiction_type_check",
        ),
        {"schema": "configuration"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    county_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.counties.id", ondelete="RESTRICT"), nullable=False)
    jurisdiction_type: Mapped[str] = mapped_column(String(30), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=sql_text("true"))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("configuration/case_types.py", '''
class CaseType(Base):
    __tablename__ = "case_types"
    __table_args__ = {"schema": "configuration"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=sql_text("true"))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("configuration/case_subtypes.py", '''
class CaseSubtype(Base):
    __tablename__ = "case_subtypes"
    __table_args__ = {"schema": "configuration"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    case_type_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.case_types.id", ondelete="RESTRICT"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=sql_text("true"))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("configuration/workflow_definitions.py", '''
class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"
    __table_args__ = (
        Index(
            "idx_workflow_def_active_unique",
            "jurisdiction_id",
            "case_subtype_id",
            unique=True,
            postgresql_where=sql_text("status = 'ACTIVE'"),
        ),
        {"schema": "configuration"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.jurisdictions.id", ondelete="RESTRICT"), nullable=False)
    case_subtype_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.case_subtypes.id", ondelete="RESTRICT"), nullable=False)
    workflow_name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("1"))
    status: Mapped[Optional[str]] = mapped_column(String(20), server_default=sql_text("'ACTIVE'"))
    effective_from: Mapped[Optional[date]] = mapped_column(Date)
    effective_to: Mapped[Optional[date]] = mapped_column(Date)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("configuration/questions.py", '''
class Question(Base):
    __tablename__ = "questions"
    __table_args__ = {"schema": "configuration"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(String(30), nullable=False)
    placeholder: Mapped[Optional[str]] = mapped_column(Text)
    help_text: Mapped[Optional[str]] = mapped_column(Text)
    validation_rules: Mapped[Optional[dict]] = mapped_column(JSONB)
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=sql_text("true"))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("configuration/workflow_questions.py", '''
class WorkflowQuestion(Base):
    __tablename__ = "workflow_questions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "question_id"),
        {"schema": "configuration"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.workflow_definitions.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.questions.id", ondelete="RESTRICT"), nullable=False)
    is_required: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=sql_text("true"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    visibility_condition: Mapped[Optional[dict]] = mapped_column(JSONB)
''')

w("configuration/document_templates.py", '''
class DocumentTemplate(Base):
    __tablename__ = "document_templates"
    __table_args__ = {"schema": "configuration"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=sql_text("true"))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("configuration/template_versions.py", '''
class TemplateVersion(Base):
    __tablename__ = "template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version"), {"schema": "configuration"})

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.document_templates.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    s3_bucket: Mapped[Optional[str]] = mapped_column(String(100))
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[Optional[date]] = mapped_column(Date)
    effective_to: Mapped[Optional[date]] = mapped_column(Date)
''')

w("configuration/document_rules.py", '''
class DocumentRule(Base):
    __tablename__ = "document_rules"
    __table_args__ = {"schema": "configuration"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.workflow_definitions.id", ondelete="CASCADE"), nullable=False)
    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.document_templates.id", ondelete="RESTRICT"), nullable=False)
    is_required: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=sql_text("true"))
    condition: Mapped[Optional[dict]] = mapped_column(JSONB)
''')

# --- operational ---
w("operational/users.py", '''
class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("idx_users_email", "email"),
        Index("idx_users_auth_token", "auth_token", unique=True, postgresql_where=sql_text("auth_token IS NOT NULL")),
        {"schema": "operational"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    auth_token: Mapped[Optional[str]] = mapped_column(Text)
    session_id: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("operational/user_profiles.py", '''
class UserProfile(Base):
    __tablename__ = "user_profiles"
    __table_args__ = {"schema": "operational"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("operational.users.id", ondelete="CASCADE"), unique=True, nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    middle_name: Mapped[Optional[str]] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("operational/cases.py", '''
class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint("status IN ('OPEN','ON_HOLD','CLOSED','DISMISSED')"),
        CheckConstraint("priority IN ('LOW','MEDIUM','HIGH','URGENT')"),
        Index("idx_cases_user", "user_id"),
        Index("idx_cases_status", "status"),
        Index("idx_cases_case_number", "case_number", unique=True, postgresql_where=sql_text("case_number IS NOT NULL")),
        {"schema": "operational"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("operational.users.id"), nullable=False)
    case_number: Mapped[Optional[str]] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    state_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.states.id"), nullable=False)
    county_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.counties.id"), nullable=False)
    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.jurisdictions.id"), nullable=False)
    case_type_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.case_types.id"), nullable=False)
    case_subtype_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.case_subtypes.id"))
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=sql_text("'OPEN'"))
    priority: Mapped[Optional[str]] = mapped_column(String(20))
    filing_deadline: Mapped[Optional[date]] = mapped_column(Date)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("false"))
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
''')

# --- integration (no webhooks) ---
w("integration/api_providers.py", '''
class ApiProvider(Base):
    __tablename__ = "api_providers"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','INACTIVE')"),
        {"schema": "integration"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False)
    base_url: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=sql_text("'ACTIVE'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("integration/api_logs.py", '''
class ApiLog(Base):
    __tablename__ = "api_logs"
    __table_args__ = (
        CheckConstraint("status IN ('SUCCESS','FAILED')"),
        Index("idx_api_logs_provider", "provider_id"),
        Index("idx_api_logs_reference", "reference_id"),
        {"schema": "integration"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("integration.api_providers.id"), nullable=False)
    reference_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    request_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    response_code: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("integration/payments.py", '''
class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(
            "fee_type IN ('COURT_FILING','PLATFORM_SERVICE','PAYMENT_PROCESSING','THIRD_PARTY','OTHER')",
            name="payments_fee_type_check",
        ),
        CheckConstraint(
            "status IN ('PENDING','PROCESSING','COMPLETED','FAILED','REFUNDED','WAIVED')",
            name="payments_status_check",
        ),
        Index("idx_payments_session", "session_id"),
        Index("idx_payments_submission", "submission_id"),
        Index("idx_payments_provider", "provider_id"),
        Index("idx_payments_status", "status"),
        Index("idx_payments_fee_type", "fee_type"),
        {"schema": "integration"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow.filing_sessions.id", ondelete="CASCADE"), nullable=False)
    submission_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow.jurisdiction_submissions.id", ondelete="SET NULL"))
    provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("integration.api_providers.id"))
    fee_type: Mapped[str] = mapped_column(String(50), nullable=False, server_default=sql_text("'COURT_FILING'"))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=sql_text("'USD'"))
    payment_method: Mapped[Optional[str]] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=sql_text("'PENDING'"))
    external_transaction_id: Mapped[Optional[str]] = mapped_column(String(255))
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

# --- workflow (no payments table here) ---
w("workflow/filing_sessions.py", '''
class FilingSession(Base):
    __tablename__ = "filing_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('IN_PROGRESS','COMPLETED','SUBMITTED','REJECTED','CANCELLED')"),
        Index("idx_fs_case", "case_id"),
        Index("idx_fs_workflow", "workflow_id"),
        Index("idx_fs_user", "user_id"),
        Index("idx_fs_status", "status"),
        {"schema": "workflow"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("operational.cases.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("operational.users.id"), nullable=False)
    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.workflow_definitions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=sql_text("'IN_PROGRESS'"))
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
    current_step: Mapped[Optional[str]] = mapped_column(String(100))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("workflow/filing_answers.py", '''
class FilingAnswer(Base):
    __tablename__ = "filing_answers"
    __table_args__ = (
        UniqueConstraint("session_id", "question_id"),
        Index("idx_answers_session", "session_id"),
        Index("idx_answers_question", "question_id"),
        {"schema": "workflow"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow.filing_sessions.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.questions.id"), nullable=False)
    answer_value: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("workflow/jurisdiction_submissions.py", '''
class JurisdictionSubmission(Base):
    __tablename__ = "jurisdiction_submissions"
    __table_args__ = (
        CheckConstraint(
            "submission_status IN ('PENDING','PROCESSING','ACCEPTED','REJECTED','UNDER_REVIEW','CANCELLED')"
        ),
        Index("idx_submission_session", "session_id"),
        {"schema": "workflow"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow.filing_sessions.id", ondelete="CASCADE"), nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("integration.api_providers.id"), nullable=False)
    reference_number: Mapped[Optional[str]] = mapped_column(String(100))
    submission_status: Mapped[str] = mapped_column(String(30), nullable=False)
    response_message: Mapped[Optional[str]] = mapped_column(Text)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    response_received_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
''')

w("workflow/workflow_events.py", '''
class WorkflowEvent(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (
        CheckConstraint("action IN ('INSERT','UPDATE','DELETE')"),
        Index("idx_events_session", "session_id"),
        Index("idx_events_type", "event_type"),
        Index("idx_events_entity", "entity_name", "entity_id"),
        {"schema": "workflow"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow.filing_sessions.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_name: Mapped[Optional[str]] = mapped_column(String(100))
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    action: Mapped[Optional[str]] = mapped_column(String(20))
    event_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    changed_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

# --- documents ---
w("documents/documents.py", '''
class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("source IN ('UPLOADED','GENERATED')"),
        CheckConstraint("status IN ('DRAFT','GENERATED','FILED')"),
        Index("idx_documents_case", "case_id"),
        Index("idx_documents_session", "session_id"),
        Index("idx_documents_source", "case_id", "source"),
        Index("idx_documents_status", "case_id", "status"),
        {"schema": "documents"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("operational.cases.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow.filing_sessions.id", ondelete="SET NULL"))
    document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[str] = mapped_column(String(100), nullable=False)
    original_file_name: Mapped[Optional[str]] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=sql_text("'DRAFT'"))
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("operational.users.id"))
    template_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.template_versions.id"))
    saved_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("operational.users.id"))
    s3_bucket: Mapped[str] = mapped_column(String(150), nullable=False)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("documents/document_versions.py", '''
class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_document_version"),
        Index("idx_document_versions_document", "document_id"),
        {"schema": "documents"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.documents.id", ondelete="CASCADE"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    s3_bucket: Mapped[str] = mapped_column(String(150), nullable=False)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

# --- conversations ---
w("conversations/conversations.py", '''
class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','COMPLETED')"),
        Index("idx_conversations_user", "user_id"),
        Index("idx_conversations_case", "case_id"),
        {"schema": "conversations"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("operational.users.id"), nullable=False)
    case_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("operational.cases.id"))
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow.filing_sessions.id"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=sql_text("'ACTIVE'"))
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("conversations/messages.py", '''
class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("sender IN ('USER','AI','SYSTEM')"),
        Index("idx_messages_conversation", "conversation_id"),
        {"schema": "conversations"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.conversations.id", ondelete="CASCADE"), nullable=False)
    sender: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

w("conversations/attachments.py", '''
class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (Index("idx_attachments_conversation", "conversation_id"), {"schema": "conversations"})

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.conversations.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.documents.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
''')

# --- knowledge ---
w("knowledge/matter_types.py", '''
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
''')

w("knowledge/jurisdictions.py", '''
class KnowledgeJurisdiction(Base):
    __tablename__ = "jurisdictions"
    __table_args__ = {"schema": "knowledge"}

    jurisdiction_id: Mapped[str] = mapped_column(Text, primary_key=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    abbreviation: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    requires_county_configuration: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=sql_text("true"))
    last_verified: Mapped[Optional[date]] = mapped_column(Date)
''')

w("knowledge/counties.py", '''
class KnowledgeCounty(Base):
    __tablename__ = "counties"
    __table_args__ = {"schema": "knowledge"}

    county_id: Mapped[str] = mapped_column(Text, primary_key=True)
    jurisdiction_id: Mapped[str] = mapped_column(Text, ForeignKey("knowledge.jurisdictions.jurisdiction_id"), nullable=False)
    county_name: Mapped[str] = mapped_column(Text, nullable=False)
    effective_date: Mapped[Optional[date]] = mapped_column(Date)
    last_verified: Mapped[Optional[date]] = mapped_column(Date)
''')

w("knowledge/sources.py", '''
class Source(Base):
    __tablename__ = "sources"
    __table_args__ = {"schema": "knowledge"}

    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    authority: Mapped[str] = mapped_column(Text, nullable=False)
    last_verified: Mapped[date] = mapped_column(Date, nullable=False)
''')

w("knowledge/kb_vectors.py", '''
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
''')

w("knowledge/vector_sources.py", '''
class VectorSource(Base):
    __tablename__ = "vector_sources"
    __table_args__ = {"schema": "knowledge"}

    content_id: Mapped[str] = mapped_column(Text, ForeignKey("knowledge.kb_vectors.content_id"), primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, ForeignKey("knowledge.sources.source_id"), primary_key=True)
''')

if __name__ == "__main__":
    print("Generated models under", ROOT)
