"""Tests for court PDF template ingest (S3 path + RDS insert order)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

import pytest

from app.adapters.data_sources.AIM_rds import RDSQueryError
from app.api.schemas.template_ingest import DuplicateTemplateError, TemplateIngestError
from app.services.template_ingest_service import (
    TemplateIngestService,
    build_template_s3_key,
    templates_root_prefix,
)
from app.utils.s3_utils import folder_name_from_prefix


def test_folder_name_from_prefix():
    assert (
        folder_name_from_prefix(
            "documents-repo/templates/TX/",
            "documents-repo/templates/",
        )
        == "TX"
    )
    assert (
        folder_name_from_prefix(
            "documents-repo/templates/TX/TX-TRAVIS-DISTRICT/",
            "documents-repo/templates/TX/",
        )
        == "TX-TRAVIS-DISTRICT"
    )


def test_build_template_s3_key_matches_existing_shape(monkeypatch):
    monkeypatch.setattr(
        "app.services.template_ingest_service.settings.S3_DOCUMENTS_REPO_PREFIX",
        "documents-repo",
    )
    key = build_template_s3_key(
        state="TX",
        jurisdiction="TX-TRAVIS-DISTRICT",
        code="TX_DIVORCE_PETITION_NO_CHILDREN",
        file_name="Divorce_Without_Children_Petition_1.pdf",
        object_id="2da9e713e426439297ab97eca6b8037e",
        version=1,
    )
    assert key == (
        "documents-repo/templates/TX/TX-TRAVIS-DISTRICT/"
        "TX_DIVORCE_PETITION_NO_CHILDREN/v1/"
        "2da9e713e426439297ab97eca6b8037e-Divorce_Without_Children_Petition_1.pdf"
    )


def test_templates_root_prefix(monkeypatch):
    monkeypatch.setattr(
        "app.services.template_ingest_service.settings.S3_DOCUMENTS_REPO_PREFIX",
        "documents-repo",
    )
    assert templates_root_prefix() == "documents-repo/templates"


@pytest.mark.asyncio
async def test_list_states_and_jurisdictions(monkeypatch):
    monkeypatch.setattr(
        "app.services.template_ingest_service.settings.S3_DOCUMENTS_REPO_PREFIX",
        "documents-repo",
    )
    monkeypatch.setattr(
        "app.services.template_ingest_service.settings.BUCKET_NAME",
        "goml-uslegalpro-dev",
    )
    service = TemplateIngestService(rds=_StubRds(), s3=_StubS3())  # type: ignore[arg-type]
    assert await service.list_states() == ["TX"]
    assert await service.list_jurisdictions("TX") == ["TX-TRAVIS-DISTRICT"]


@pytest.mark.asyncio
async def test_list_jurisdictions_unknown_state(monkeypatch):
    monkeypatch.setattr(
        "app.services.template_ingest_service.settings.S3_DOCUMENTS_REPO_PREFIX",
        "documents-repo",
    )
    monkeypatch.setattr(
        "app.services.template_ingest_service.settings.BUCKET_NAME",
        "goml-uslegalpro-dev",
    )
    service = TemplateIngestService(rds=_StubRds(), s3=_StubS3())  # type: ignore[arg-type]
    with pytest.raises(TemplateIngestError) as exc:
        await service.list_jurisdictions("CA")
    assert exc.value.status_code == 404


class _StubS3:
    def __init__(self) -> None:
        self.prefixes: Dict[str, List[str]] = {
            "documents-repo/templates": ["TX"],
            "documents-repo/templates/TX": ["TX-TRAVIS-DISTRICT"],
        }
        self.uploads: List[Dict[str, Any]] = []
        self.deletes: List[str] = []

    async def list_common_prefixes(self, prefix: str, *, bucket: Optional[str] = None):
        key = prefix.strip("/")
        return list(self.prefixes.get(key, []))

    async def upload_bytes_at_key(self, data, s3_key, *, bucket=None, content_type=None):
        self.uploads.append(
            {"key": s3_key, "bucket": bucket, "size": len(data), "content_type": content_type}
        )
        return s3_key

    async def delete_file(self, s3_key: str, bucket: Optional[str] = None):
        self.deletes.append(s3_key)


class _StubRds:
    def __init__(self, existing: Optional[Dict[str, Any]] = None, fail: Optional[Exception] = None):
        self.existing = existing
        self.fail = fail
        self.calls: List[str] = []

    async def fetch_one(self, query: str, *params: Any):
        sql = " ".join(query.split()).lower()
        if "from configuration.document_templates" in sql and "where dt.code" in sql:
            self.calls.append("fetch_by_code")
            return self.existing
        if "insert into configuration.document_templates" in sql:
            self.calls.append("insert_template")
            if self.fail:
                raise self.fail
            return {
                "id": params[0],
                "code": params[1],
                "name": params[2],
            }
        if "insert into configuration.template_versions" in sql:
            self.calls.append("insert_version")
            return {
                "id": params[0],
                "template_id": params[1],
                "version": params[2],
                "s3_bucket": params[3],
                "s3_key": params[4],
            }
        raise AssertionError(f"unexpected query: {query[:80]}")


def test_patch_openapi_adds_s3_folder_enums(monkeypatch):
    from app.api.endpoints.template_ingest import patch_template_ingest_openapi

    monkeypatch.setattr(
        "app.api.endpoints.template_ingest.folder_tree_sync",
        lambda: {"TX": ["TX-TRAVIS-DISTRICT"]},
    )
    schema = {
        "paths": {
            "/api/templates/ingest": {
                "post": {
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "properties": {
                                        "state": {"type": "string"},
                                        "jurisdiction": {"type": "string"},
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    patch_template_ingest_openapi(schema)
    props = schema["paths"]["/api/templates/ingest"]["post"]["requestBody"][
        "content"
    ]["multipart/form-data"]["schema"]["properties"]
    assert props["state"]["enum"] == ["TX"]
    assert props["jurisdiction"]["enum"] == ["TX-TRAVIS-DISTRICT"]


@pytest.mark.asyncio
async def test_ingest_inserts_template_then_version(monkeypatch):
    monkeypatch.setattr(
        "app.services.template_ingest_service.settings.S3_DOCUMENTS_REPO_PREFIX",
        "documents-repo",
    )
    monkeypatch.setattr(
        "app.services.template_ingest_service.settings.BUCKET_NAME",
        "goml-uslegalpro-dev",
    )
    s3 = _StubS3()
    rds = _StubRds()
    service = TemplateIngestService(rds=rds, s3=s3)  # type: ignore[arg-type]
    result = await service.ingest(
        file_bytes=b"%PDF-1.4 fake",
        file_name="Waiver_of_Service_2.pdf",
        code="TX_DIVORCE_WAIVER_OF_SERVICE",
        name="Waiver of Service",
        doc_type="PETITION_WAIVER_OF_SERVICE",
        state="TX",
        jurisdiction="TX-TRAVIS-DISTRICT",
        case_category="Family_Marriage Relationship",
        case_type="DIVORCE",
        case_subtype="WITH_CHILDREN",
        field_mapping='{"id":""}',
        sample_input="{}",
    )
    assert rds.calls == ["fetch_by_code", "insert_template", "insert_version"]
    assert len(s3.uploads) == 1
    assert s3.uploads[0]["bucket"] == "goml-uslegalpro-dev"
    assert "/TX/TX-TRAVIS-DISTRICT/TX_DIVORCE_WAIVER_OF_SERVICE/v1/" in result["s3_key"]
    assert result["s3_key"].endswith("-Waiver_of_Service_2.pdf")
    assert result["version"] == 1
    UUID(str(result["template_id"]))
    UUID(str(result["version_id"]))
    assert result["s3_uri"].startswith("s3://goml-uslegalpro-dev/")


@pytest.mark.asyncio
async def test_ingest_rejects_duplicate_code(monkeypatch):
    monkeypatch.setattr(
        "app.services.template_ingest_service.settings.S3_DOCUMENTS_REPO_PREFIX",
        "documents-repo",
    )
    monkeypatch.setattr(
        "app.services.template_ingest_service.settings.BUCKET_NAME",
        "goml-uslegalpro-dev",
    )
    service = TemplateIngestService(
        rds=_StubRds(existing={"template_id": "00f53d19-6843-4b69-bece-58ff10a616cb"}),
        s3=_StubS3(),
    )  # type: ignore[arg-type]
    with pytest.raises(DuplicateTemplateError) as exc:
        await service.ingest(
            file_bytes=b"%PDF-1.4 fake",
            file_name="Waiver.pdf",
            code="TX_DIVORCE_WAIVER_OF_SERVICE",
            name="Waiver of Service",
            state="TX",
            jurisdiction="TX-TRAVIS-DISTRICT",
        )
    assert exc.value.template_id == "00f53d19-6843-4b69-bece-58ff10a616cb"


@pytest.mark.asyncio
async def test_ingest_deletes_s3_when_rds_insert_fails(monkeypatch):
    monkeypatch.setattr(
        "app.services.template_ingest_service.settings.S3_DOCUMENTS_REPO_PREFIX",
        "documents-repo",
    )
    monkeypatch.setattr(
        "app.services.template_ingest_service.settings.BUCKET_NAME",
        "goml-uslegalpro-dev",
    )
    s3 = _StubS3()
    rds = _StubRds(fail=RDSQueryError("insert failed"))
    service = TemplateIngestService(rds=rds, s3=s3)  # type: ignore[arg-type]
    with pytest.raises(TemplateIngestError):
        await service.ingest(
            file_bytes=b"%PDF-1.4 fake",
            file_name="Waiver.pdf",
            code="TX_NEW_TEMPLATE",
            name="New",
            state="TX",
            jurisdiction="TX-TRAVIS-DISTRICT",
        )
    assert s3.uploads
    assert s3.deletes == [s3.uploads[0]["key"]]


@pytest.mark.asyncio
async def test_list_folder_tree_groups_jurisdictions_by_state(monkeypatch):
    monkeypatch.setattr(
        "app.services.template_ingest_service.settings.S3_DOCUMENTS_REPO_PREFIX",
        "documents-repo",
    )
    monkeypatch.setattr(
        "app.services.template_ingest_service.settings.BUCKET_NAME",
        "goml-uslegalpro-dev",
    )
    service = TemplateIngestService(rds=_StubRds(), s3=_StubS3())  # type: ignore[arg-type]
    tree = await service.list_folder_tree()
    assert tree == {"TX": ["TX-TRAVIS-DISTRICT"]}


@pytest.mark.asyncio
async def test_ingest_rejects_unknown_jurisdiction_folder(monkeypatch):
    monkeypatch.setattr(
        "app.services.template_ingest_service.settings.S3_DOCUMENTS_REPO_PREFIX",
        "documents-repo",
    )
    monkeypatch.setattr(
        "app.services.template_ingest_service.settings.BUCKET_NAME",
        "goml-uslegalpro-dev",
    )
    service = TemplateIngestService(rds=_StubRds(), s3=_StubS3())  # type: ignore[arg-type]
    with pytest.raises(TemplateIngestError) as exc:
        await service.ingest(
            file_bytes=b"%PDF-1.4 fake",
            file_name="Waiver.pdf",
            code="TX_NEW_TEMPLATE",
            name="New",
            state="TX",
            jurisdiction="missing-court",
        )
    assert exc.value.status_code == 404
