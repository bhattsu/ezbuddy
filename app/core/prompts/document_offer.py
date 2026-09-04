"""Fixed copy for the document-offer step (not an LLM prompt)."""

DOCUMENT_OFFER_USER_MESSAGE = "[document_offer]"

DOCUMENT_OFFER_MESSAGE = (
    "I can pre-fill some of your filing answers if you have related documents. "
    "Do you have any PDF or Word files to upload? If not, we can continue with a few questions."
)

DOCUMENT_OFFER_NO_TEMPLATES = (
    "I will collect the information needed for your filing. "
    "Currently there are no document templates present for this case. "
    "Do you have any PDF or Word files to upload to pre-fill answers? "
    "If not, we can continue with a few questions."
)

DOCUMENTS_READY_WITH_REQUIRED = (
    "Filing answers are complete.\n\n"
    "Required documents for this case:\n{doc_list}\n\n"
    "{summary}"
)

DOCUMENTS_READY_NO_TEMPLATES = (
    "Filing answers are complete. "
    "Currently there are no document templates present for this case, "
    "so no court forms were generated. Your answers have been saved."
)

DOCUMENT_OFFER_WITH_REQUIRED = (
    "For this filing the court typically needs: {doc_list}. "
    "Do you already have any of these documents to upload? "
    "If you upload them I can pre-fill answers and skip questions we already know. "
    "If not, we can continue with a few questions."
)

AWAITING_UPLOAD_MESSAGE = (
    "Please upload one or more PDF or Word files. You can send several at once or one at a time. "
    "When you are finished uploading, say that is all and we will continue with any remaining questions."
)

MORE_UPLOADS_MESSAGE = (
    "I pre-filled {count} field(s) from your document(s). "
    "You can upload more files, or say that is all to continue with the remaining questions."
)
