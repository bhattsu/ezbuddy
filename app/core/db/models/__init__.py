"""Load all ORM models onto Base.metadata."""

from app.core.db.models.configuration.case_subtypes import CaseSubtype
from app.core.db.models.configuration.case_types import CaseType
from app.core.db.models.configuration.counties import County
from app.core.db.models.configuration.document_rules import DocumentRule
from app.core.db.models.configuration.document_templates import DocumentTemplate
from app.core.db.models.configuration.jurisdictions import Jurisdiction
from app.core.db.models.configuration.package_imports import PackageImport
from app.core.db.models.configuration.questions import Question
from app.core.db.models.configuration.states import State
from app.core.db.models.configuration.template_versions import TemplateVersion
from app.core.db.models.configuration.workflow_definitions import WorkflowDefinition
from app.core.db.models.configuration.workflow_questions import WorkflowQuestion
from app.core.db.models.conversations.attachments import Attachment
from app.core.db.models.conversations.conversations import Conversation
from app.core.db.models.conversations.messages import Message
from app.core.db.models.documents.document_versions import DocumentVersion
from app.core.db.models.documents.documents import Document
from app.core.db.models.integration.api_logs import ApiLog
from app.core.db.models.integration.api_providers import ApiProvider
from app.core.db.models.integration.payments import Payment
from app.core.db.models.knowledge.counties import KnowledgeCounty
from app.core.db.models.knowledge.jurisdictions import KnowledgeJurisdiction
from app.core.db.models.knowledge.kb_vectors import KbVector
from app.core.db.models.knowledge.matter_types import MatterType
from app.core.db.models.knowledge.sources import Source
from app.core.db.models.knowledge.vector_sources import VectorSource
from app.core.db.models.operational.cases import Case
from app.core.db.models.operational.user_profiles import UserProfile
from app.core.db.models.operational.users import User
from app.core.db.models.workflow.filing_answers import FilingAnswer
from app.core.db.models.workflow.filing_sessions import FilingSession
from app.core.db.models.workflow.jurisdiction_submissions import JurisdictionSubmission
from app.core.db.models.workflow.workflow_events import WorkflowEvent

__all__ = [
    "State", "County", "Jurisdiction", "CaseType", "CaseSubtype", "WorkflowDefinition",
    "Question", "WorkflowQuestion", "DocumentTemplate", "TemplateVersion", "DocumentRule",
    "PackageImport",
    "User", "UserProfile", "Case", "ApiProvider", "ApiLog", "Payment",
    "FilingSession", "FilingAnswer", "JurisdictionSubmission", "WorkflowEvent",
    "Document", "DocumentVersion", "Conversation", "Message", "Attachment",
    "MatterType", "KnowledgeJurisdiction", "KnowledgeCounty", "Source", "KbVector", "VectorSource",
]
