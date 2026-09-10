"""Court PDF template ingest: S3 folder lists + upload + configuration RDS rows."""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from html import escape
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse

from app.api.schemas.rag import ErrorResponse
from app.api.schemas.template_ingest import (
    DuplicateTemplateError,
    TemplateFolderListResponse,
    TemplateIngestError,
    TemplateIngestResponse,
)
from app.services.aim_factory import get_or_create_rds_repo
from app.services.template_ingest_service import (
    TemplateIngestService,
    templates_root_prefix,
)
from app.utils.s3_utils import S3Manager

logger = logging.getLogger(__name__)
router = APIRouter()

_FOLDER_TREE_TTL_SEC = 60.0
_folder_tree_cache: Dict[str, Any] = {"at": 0.0, "tree": {}}


def _s3_service() -> TemplateIngestService:
    class _NoRds:
        async def fetch_one(self, *args, **kwargs):
            raise RuntimeError("RDS is required for template ingest")

    return TemplateIngestService(rds=_NoRds(), s3=S3Manager())  # type: ignore[arg-type]


async def _service(request: Request) -> TemplateIngestService:
    try:
        rds = await get_or_create_rds_repo(request.app)
    except Exception as exc:  # noqa: BLE001
        logger.error("RDS unavailable for template ingest: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    if rds is None:
        raise HTTPException(status_code=503, detail="Database not configured")
    return TemplateIngestService(rds=rds, s3=S3Manager())


def folder_tree_sync() -> Dict[str, List[str]]:
    """Cached S3 folder map for Swagger dropdowns: {state: [jurisdiction, ...]}."""
    now = time.time()
    cached = _folder_tree_cache.get("tree") or {}
    if cached and now - float(_folder_tree_cache.get("at") or 0) < _FOLDER_TREE_TTL_SEC:
        return dict(cached)
    try:
        manager = S3Manager()
        root = templates_root_prefix()
        tree: Dict[str, List[str]] = {}
        for state in manager.list_common_prefixes_sync(root):
            tree[state] = manager.list_common_prefixes_sync(f"{root}/{state}")
        _folder_tree_cache["at"] = now
        _folder_tree_cache["tree"] = tree
        return tree
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not list template S3 folders for dropdowns: %s", exc)
        return dict(cached)


def patch_template_ingest_openapi(schema: Dict[str, Any]) -> None:
    """Put S3 state/jurisdiction folders onto POST /api/templates/ingest in Swagger."""
    path_item = (schema.get("paths") or {}).get("/api/templates/ingest") or {}
    post = path_item.get("post") or {}
    content = (
        ((post.get("requestBody") or {}).get("content") or {}).get("multipart/form-data")
        or {}
    )
    props = ((content.get("schema") or {}).get("properties") or {})
    tree = folder_tree_sync()
    states = sorted(tree)
    jurisdictions = sorted({name for names in tree.values() for name in names})
    if states and "state" in props:
        props["state"]["enum"] = states
        props["state"]["description"] = (
            "S3 folder under documents-repo/templates/. Choose this first; "
            "jurisdiction options belong to the selected state. "
            + json.dumps(tree, separators=(",", ":"))
        )
    if jurisdictions and "jurisdiction" in props:
        props["jurisdiction"]["enum"] = jurisdictions
        props["jurisdiction"]["description"] = (
            "S3 folder under the selected state. Disabled in the browser form "
            "until a state is chosen. Map: "
            + json.dumps(tree, separators=(",", ":"))
        )


@router.get(
    "/states",
    response_model=TemplateFolderListResponse,
    responses={503: {"model": ErrorResponse}},
    summary="List template state folders",
    description="Immediate child folders of documents-repo/templates/ in S3.",
)
async def list_template_states():
    service = _s3_service()
    try:
        options = await service.list_states()
    except TemplateIngestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return TemplateFolderListResponse(prefix=templates_root_prefix(), options=options)


@router.get(
    "/jurisdictions",
    response_model=TemplateFolderListResponse,
    responses={
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    summary="List template jurisdiction folders",
    description="Immediate child folders of documents-repo/templates/{state}/ in S3.",
)
async def list_template_jurisdictions(
    state: str = Query(..., description="State folder name from GET /api/templates/states"),
):
    service = _s3_service()
    try:
        options = await service.list_jurisdictions(state)
    except TemplateIngestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return TemplateFolderListResponse(
        prefix=f"{templates_root_prefix()}/{state.strip()}",
        options=options,
    )


@router.get(
    "/ingest",
    response_class=HTMLResponse,
    include_in_schema=False,
    summary="Template ingest form",
)
async def template_ingest_form():
    """Browser form: pick S3 state, then jurisdiction, upload PDF, submit details."""
    service = _s3_service()
    try:
        tree = await service.list_folder_tree()
    except TemplateIngestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return HTMLResponse(_ingest_form_html(tree))


@router.post(
    "/ingest",
    response_model=TemplateIngestResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    summary="Ingest a court PDF template",
    description=(
        "Upload a PDF and metadata in one call. State and jurisdiction are S3 folders "
        "under documents-repo/templates/ (dropdowns in /docs and on GET this URL). "
        "Choose state first; jurisdiction is the court folder under that state. "
        "The file is stored at templates/{state}/{jurisdiction}/{code}/v1/ "
        "and rows are inserted into configuration.document_templates then template_versions."
    ),
)
async def ingest_court_template(
    request: Request,
    file: UploadFile = File(..., description="Court form PDF"),
    code: str = Form(..., description="Filename / template code"),
    name: str = Form(...),
    description: str = Form(""),
    doc_type: str = Form(""),
    state: str = Form(
        ...,
        description="S3 state folder under documents-repo/templates/ (dropdown from S3)",
    ),
    jurisdiction: str = Form(
        ...,
        description="S3 jurisdiction folder under the selected state (activates after state)",
    ),
    case_category: str = Form(""),
    case_type: str = Form(""),
    case_subtype: str = Form(""),
    field_mapping: str = Form("", description="doc_gen field mapping text or JSON"),
    sample_input: str = Form("", description="doc_gen sample output JSON"),
    effective_from: Optional[date] = Form(None),
    effective_to: Optional[date] = Form(None),
):
    service = await _service(request)
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    try:
        result = await service.ingest(
            file_bytes=file_bytes,
            file_name=file.filename or "template.pdf",
            code=code,
            name=name,
            description=description,
            doc_type=doc_type,
            state=state,
            jurisdiction=jurisdiction,
            case_category=case_category,
            case_type=case_type,
            case_subtype=case_subtype,
            field_mapping=field_mapping,
            sample_input=sample_input,
            effective_from=effective_from,
            effective_to=effective_to,
        )
    except DuplicateTemplateError as exc:
        detail = f"Template code already exists: {exc.code}"
        if exc.template_id:
            detail = f"{detail} (id={exc.template_id})"
        raise HTTPException(status_code=409, detail=detail) from exc
    except TemplateIngestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return TemplateIngestResponse.model_validate(result)


def _ingest_form_html(tree: Dict[str, List[str]]) -> str:
    states = "".join(
        f'<option value="{escape(state)}">{escape(state)}</option>'
        for state in sorted(tree)
    )
    tree_json = json.dumps(tree).replace("<", "\\u003c")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Court template ingest</title>
  <style>
    body {{ font-family: sans-serif; max-width: 720px; margin: 2rem auto; color: #222; }}
    label {{ display: block; margin-top: 0.8rem; font-weight: 600; }}
    input, select, textarea {{ width: 100%; padding: 0.4rem; box-sizing: border-box; }}
    textarea {{ min-height: 5rem; font-family: monospace; }}
    button {{ margin-top: 1.2rem; padding: 0.6rem 1.2rem; }}
    .hint {{ color: #555; font-weight: 400; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <h1>Ingest court PDF template</h1>
  <p>Upload a PDF, choose an S3 state folder, then a jurisdiction folder under that state.
     Submit once to store the file and insert RDS rows.</p>
  <form id="ingest" method="post" enctype="multipart/form-data">
    <label>PDF file <input type="file" name="file" accept="application/pdf" required/></label>
    <label>State <span class="hint">(folders in documents-repo/templates/)</span>
      <select name="state" id="state" required>
        <option value="">Select state</option>
        {states}
      </select>
    </label>
    <label>Jurisdiction <span class="hint">(activates after state)</span>
      <select name="jurisdiction" id="jurisdiction" required disabled>
        <option value="">Select state first</option>
      </select>
    </label>
    <label>code <input name="code" required maxlength="50" placeholder="TX_DIVORCE_WAIVER_OF_SERVICE"/></label>
    <label>name <input name="name" required placeholder="Waiver of Service"/></label>
    <label>description <input name="description"/></label>
    <label>doc_type <input name="doc_type" placeholder="PETITION_WAIVER_OF_SERVICE"/></label>
    <label>case_category <input name="case_category" placeholder="Family_Marriage Relationship"/></label>
    <label>case_type <input name="case_type" placeholder="DIVORCE"/></label>
    <label>case_subtype <input name="case_subtype" placeholder="WITH_CHILDREN"/></label>
    <label>field_mapping <textarea name="field_mapping"></textarea></label>
    <label>sample_input <textarea name="sample_input"></textarea></label>
    <label>effective_from <input type="date" name="effective_from"/></label>
    <label>effective_to <input type="date" name="effective_to"/></label>
    <button type="submit">Upload and ingest</button>
  </form>
  <script>
    const tree = {tree_json};
    const form = document.getElementById("ingest");
    const stateEl = document.getElementById("state");
    const jurisEl = document.getElementById("jurisdiction");
    function fillJurisdictions() {{
      const state = stateEl.value;
      const folders = tree[state] || [];
      jurisEl.innerHTML = "";
      if (!state) {{
        jurisEl.disabled = true;
        jurisEl.appendChild(new Option("Select state first", ""));
        return;
      }}
      jurisEl.disabled = false;
      jurisEl.appendChild(new Option("Select jurisdiction", ""));
      folders.forEach((name) => jurisEl.appendChild(new Option(name, name)));
    }}
    stateEl.addEventListener("change", fillJurisdictions);
    fillJurisdictions();
    form.addEventListener("submit", () => {{
      ["effective_from", "effective_to"].forEach((name) => {{
        const el = form.elements[name];
        if (el && !el.value) el.disabled = true;
      }});
    }});
  </script>
</body>
</html>
"""
