"""Orchestration dependency bundle (repos + sub-agents)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.adapters.llm.bedrock import Bedrock, get_bedrock
from app.agents.conversation.filing_assistant_agent import FilingAssistantAgent
from app.agents.conversation.workflow_match_agent import WorkflowMatchAgent
from app.agents.conversation.workflow_questions_agent import WorkflowQuestionsAgent
from app.services.conversation_repository import ConversationRepository
from app.services.court_rules_service import CourtRulesService
from app.services.document_analysis_service import DocumentAnalysisService
from app.services.document_generation_service import DocumentGenerationService
from app.services.legal_filing_repository import LegalFilingRepository
from app.services.operational_user_repository import OperationalUserRepository
from app.services.uslegalpro_codes_service import USLegalProCodesService
from app.services.uslegalpro_existing_case_service import (
    USLegalProExistingCaseService,
)
from app.utils.s3_utils import S3Manager


@dataclass
class FilingOrchestratorContext:
    """Shared dependencies injected into LangGraph node closures."""

    filing_repo: LegalFilingRepository
    conversation_repo: ConversationRepository
    nav_agent: FilingAssistantAgent
    workflow_match_agent: WorkflowMatchAgent
    workflow_agent: WorkflowQuestionsAgent
    analysis_service: DocumentAnalysisService
    generation_service: DocumentGenerationService
    bedrock: Bedrock
    court_rules_service: CourtRulesService
    codes_service: USLegalProCodesService = field(
        default_factory=USLegalProCodesService
    )
    existing_case_service: USLegalProExistingCaseService = field(
        default_factory=USLegalProExistingCaseService
    )
    user_repo: Optional[OperationalUserRepository] = None
    s3_manager: Optional[S3Manager] = None
    notifications: List[Dict[str, Any]] = field(default_factory=list)
    on_notification: Optional[Callable[[Dict[str, Any]], Awaitable[None] | None]] = None

    async def notify(
        self,
        process: str,
        message: Optional[str] = None,
        level: Optional[str] = None,
    ) -> Dict[str, Any]:
        from app.services.process_notifications import build_notification

        item = build_notification(process, message=message, level=level)
        if any(row.get("process") == item["process"] for row in self.notifications):
            return item
        self.notifications.append(item)
        callback = self.on_notification
        if callback is not None:
            maybe = callback(item)
            if maybe is not None:
                await maybe
        return item

    @classmethod
    def create(
        cls,
        filing_repo: Optional[LegalFilingRepository] = None,
        conversation_repo: Optional[ConversationRepository] = None,
        nav_agent: Optional[FilingAssistantAgent] = None,
        workflow_match_agent: Optional[WorkflowMatchAgent] = None,
        workflow_agent: Optional[WorkflowQuestionsAgent] = None,
        analysis_service: Optional[DocumentAnalysisService] = None,
        generation_service: Optional[DocumentGenerationService] = None,
        bedrock: Optional[Bedrock] = None,
        court_rules_service: Optional[CourtRulesService] = None,
        codes_service: Optional[USLegalProCodesService] = None,
        existing_case_service: Optional[USLegalProExistingCaseService] = None,
        user_repo: Optional[OperationalUserRepository] = None,
        s3_manager: Optional[S3Manager] = None,
    ) -> "FilingOrchestratorContext":
        shared = bedrock or get_bedrock()
        filing_repo = filing_repo or LegalFilingRepository()
        conversation_repo = conversation_repo or ConversationRepository()
        s3 = s3_manager
        if s3 is None:
            try:
                s3 = S3Manager()
            except Exception:
                s3 = None
        return cls(
            filing_repo=filing_repo,
            conversation_repo=conversation_repo,
            nav_agent=nav_agent or FilingAssistantAgent(shared),
            workflow_match_agent=workflow_match_agent or WorkflowMatchAgent(shared),
            workflow_agent=workflow_agent or WorkflowQuestionsAgent(shared),
            analysis_service=analysis_service or DocumentAnalysisService(),
            generation_service=generation_service or DocumentGenerationService(),
            bedrock=shared,
            court_rules_service=court_rules_service
            or CourtRulesService(embedder=shared, llm_client=shared),
            codes_service=codes_service or USLegalProCodesService(),
            existing_case_service=existing_case_service
            or USLegalProExistingCaseService(),
            user_repo=user_repo
            or OperationalUserRepository(rds=filing_repo.rds),
            s3_manager=s3,
        )

    async def require_conversation_repo(self) -> None:
        if self.conversation_repo.rds is None:
            raise RuntimeError(
                "RDS is not configured. Conversation persistence is required."
            )
