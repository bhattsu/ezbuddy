"""Simple process toasts for the filing chat WebSocket.

Each toast stays on screen until the next notification replaces it.
In-progress keys (loading_*, extracting_*, searching_*) are sent live while
work runs; phase keys are sent when that step is ready for the user.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

NotificationLevel = str

# process -> (message, level)
PROCESS_TOASTS: Dict[str, tuple[str, NotificationLevel]] = {
    "session_started": ("Filing chat is ready", "success"),
    "processing_request": ("Processing your request", "info"),
    "greeting": ("Starting the filing chat", "info"),
    "loading_states": ("Loading the states", "info"),
    "selecting_state": ("Select the filing state", "info"),
    "intent_pending": ("Choose new case or existing case", "info"),
    "loading_counties": ("Loading the counties", "info"),
    "selecting_county": ("Select the county", "info"),
    "loading_courts": ("Loading the courts", "info"),
    "selecting_jurisdiction": ("Select the court", "info"),
    "loading_case_categories": ("Loading the case categories", "info"),
    "selecting_case_category": ("Select the case category", "info"),
    "loading_case_types": ("Loading the case types", "info"),
    "selecting_case_type": ("Select the case type", "info"),
    "loading_party_types": ("Loading the filing parties", "info"),
    "selecting_case_parties": ("Select the filing party", "info"),
    "loading_filer_types": ("Loading the filer types", "info"),
    "selecting_filer_type": ("Select the filer type", "info"),
    "loading_filing_codes": ("Loading the filing codes", "info"),
    "selecting_filing_code": ("Select the filing code", "info"),
    "loading_doc_type_codes": ("Loading the court document types", "info"),
    "selecting_doc_type_code": ("Select the court document type", "info"),
    "loading_document_types": ("Loading the document types", "info"),
    "selecting_document_type": ("Select the document to file", "info"),
    "loading_filing_types": ("Loading the filing types", "info"),
    "selecting_filing_type": ("Select the filing type", "info"),
    "extracting_template": ("Extracting the questions", "info"),
    "extracting_questions": ("Extracting the questions", "info"),
    "loading_questions": ("Loading the questions", "info"),
    "questions_ready": ("Form questions are ready", "success"),
    "matching_workflow": ("Matching the case workflow", "info"),
    "collecting_workflow_answers": ("Collecting form answers", "info"),
    "offering_documents": ("You can upload supporting documents", "info"),
    "awaiting_document_upload": ("Waiting for document upload", "info"),
    "analyzing_upload": ("Analyzing the uploaded document", "info"),
    "prefilling_answers": ("Filling answers from the upload", "info"),
    "analysis_complete": ("Document analysis finished", "success"),
    "generating_documents": ("Building the form JSON", "info"),
    "mapping_fields": ("Mapping answers onto template fields", "info"),
    "generating_court_document": ("Generating case_document.pdf", "info"),
    "documents_ready": ("case_document.pdf is ready", "success"),
    "verifying_platform_payment": ("Verifying US Legal Pro payment", "info"),
    "verifying_court_payment": ("Loading court payment accounts", "info"),
    "confirming_efile": ("Review the e-file request", "info"),
    "submitting_efile": ("Submitting your filing to the court", "info"),
    "checking_envelope_status": ("Checking envelope status", "info"),
    "existing_lookup_method": ("Choose how to find the existing case", "info"),
    "existing_selecting_state": ("Select the filing state", "info"),
    "existing_selecting_jurisdiction": ("Select the court", "info"),
    "existing_enter_case_number": ("Enter the existing case number", "info"),
    "searching_existing_case": ("Searching for the existing case", "info"),
    "loading_case_details": ("Loading the case details", "info"),
    "existing_search_party": ("Searching by party name", "info"),
    "existing_search_date": ("Searching by filing date", "info"),
    "existing_case_confirm": ("Confirm the existing case", "info"),
    "existing_upload_analysis": ("Reading the uploaded case document", "info"),
    "case_located": ("Existing case found", "success"),
    "answering_legal_question": ("Answering your legal question", "info"),
    "generic_legal": ("Legal question answered", "success"),
    "complete": ("Filing is complete", "success"),
    "error": ("Something went wrong", "error"),
}

EVENT_KIND_PROCESS = {
    "session.started": "session_started",
    "documents.offer": "offering_documents",
    "analysis.complete": "analysis_complete",
    "documents.ready": "documents_ready",
    "payment.platform": "verifying_platform_payment",
    "payment.court": "verifying_court_payment",
    "efile.confirm": "confirming_efile",
    "case.located": "case_located",
    "workflow.complete": "collecting_workflow_answers",
    "error": "error",
}

# Shown immediately while a phase loads options or does work.
PHASE_LOADING_PROCESS: Dict[str, str] = {
    "greeting": "session_started",
    "selecting_state": "loading_states",
    "existing_selecting_state": "loading_states",
    "selecting_county": "loading_counties",
    "selecting_jurisdiction": "loading_courts",
    "existing_selecting_jurisdiction": "loading_courts",
    "selecting_case_category": "loading_case_categories",
    "selecting_case_type": "loading_case_types",
    "selecting_case_parties": "loading_party_types",
    "selecting_filer_type": "loading_filer_types",
    "selecting_filing_code": "loading_filing_codes",
    "selecting_doc_type_code": "loading_doc_type_codes",
    "selecting_document_type": "loading_document_types",
    "selecting_filing_type": "loading_filing_types",
    "collecting_workflow_answers": "loading_questions",
    "offering_documents": "offering_documents",
    "awaiting_document_upload": "awaiting_document_upload",
    "generating_documents": "generating_documents",
    "verifying_platform_payment": "verifying_platform_payment",
    "verifying_court_payment": "verifying_court_payment",
    "confirming_efile": "confirming_efile",
    "submitting_efile": "submitting_efile",
    "checking_envelope_status": "checking_envelope_status",
    "existing_enter_case_number": "existing_enter_case_number",
    "existing_search_party": "existing_search_party",
    "existing_search_date": "existing_search_date",
    "existing_case_confirm": "existing_case_confirm",
    "existing_upload_analysis": "existing_upload_analysis",
    "existing_lookup_method": "existing_lookup_method",
    "intent_pending": "intent_pending",
}


def phase_value(phase: Any) -> str:
    if phase is None:
        return ""
    return str(getattr(phase, "value", None) or phase)


def loading_process_for_phase(phase: Any) -> Optional[str]:
    """In-progress toast key for a phase, if one should be shown while work runs."""
    return PHASE_LOADING_PROCESS.get(phase_value(phase))


def build_notification(
    process: str,
    *,
    message: Optional[str] = None,
    level: Optional[str] = None,
) -> Dict[str, str]:
    default_message, default_level = PROCESS_TOASTS.get(
        process, (process.replace("_", " ").strip() or "Update", "info")
    )
    text = (message or default_message).strip() or default_message
    return {
        "process": process,
        "message": text,
        "level": (level or default_level).strip() or default_level,
    }


def notifications_for_result(
    *,
    event_kind: str,
    phase: Any,
    assistant_message: str = "",
    mode: Any = None,
) -> List[Dict[str, str]]:
    """One simple toast for the completed process on this chat turn."""
    kind = str(event_kind or "")
    if kind == "error":
        return [
            build_notification(
                "error",
                message=assistant_message or None,
                level="error",
            )
        ]
    process = EVENT_KIND_PROCESS.get(kind)
    mode_value = phase_value(mode)
    if not process and mode_value == "generic" and kind == "assistant.message":
        process = "generic_legal"
    if not process and phase is not None:
        process = phase_value(phase)
    if not process:
        return []
    return [build_notification(process)]


def merge_notifications(
    existing: Iterable[Dict[str, Any]],
    extra: Iterable[Dict[str, Any]],
) -> List[Dict[str, str]]:
    merged: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in list(existing or []) + list(extra or []):
        if not isinstance(item, dict):
            continue
        process = str(item.get("process") or "").strip()
        message = str(item.get("message") or "").strip()
        if not process or process in seen:
            continue
        seen.add(process)
        merged.append(
            build_notification(
                process,
                message=message or None,
                level=str(item.get("level") or "") or None,
            )
        )
    return merged
