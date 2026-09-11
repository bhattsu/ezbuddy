"""Court-document gate after Textract / analysis extraction."""

import pytest

from app.services.court_document_validator import (
    NON_COURT_DOCUMENT_REJECTION_MESSAGE,
    NonCourtDocumentError,
    is_court_document,
    validate_court_document,
)


def test_accepts_court_petition_text():
    text = (
        "IN THE DISTRICT COURT OF TRAVIS COUNTY, TEXAS "
        "CAUSE NO. D-1-FM-26-0001 Original Petition for Divorce "
        "Petitioner Jane Doe Respondent John Doe"
    )
    assert is_court_document(text=text, classification="petition")


def test_rejects_resume_and_invoice():
    assert not is_court_document(
        text="Curriculum Vitae Work Experience References Available Education",
        classification="other",
    )
    assert not is_court_document(
        text="Invoice Number 1042 Total Due $500 Payment Terms Net 30",
        classification="other",
    )


def test_rejects_aws_pricing_estimate_like_user_upload():
    """Empty petitioner keys must not count as court signals."""
    extracted_fields = {
        "document_type": "AWS Pricing Calculator Estimate",
        "summary.monthly_cost": "1,147.93 USD",
        "services[0].name": "Amazon Bedrock",
        "services[1].name": "Amazon DynamoDB",
    }
    user_details = {
        "cause_number": "",
        "court_name": "",
        "county": "",
        "petitioner": {"full_name": "", "address": ""},
        "respondent": {"full_name": "", "address": ""},
    }
    assert not is_court_document(
        extracted_fields=extracted_fields,
        user_details=user_details,
        classification="other",
        case_type="Cloud Infrastructure Cost Estimate",
        sub_case_type="AWS Pricing Calculator Estimate",
    )
    with pytest.raises(NonCourtDocumentError):
        validate_court_document(
            extracted_fields=extracted_fields,
            user_details=user_details,
            classification="other",
            case_type="Cloud Infrastructure Cost Estimate",
            sub_case_type="AWS Pricing Calculator Estimate",
        )


def test_validate_raises_for_non_court():
    with pytest.raises(NonCourtDocumentError, match="not a court document"):
        validate_court_document(text="Meeting agenda for the sales team", classification="other")
    assert "no file" in NON_COURT_DOCUMENT_REJECTION_MESSAGE.lower()
