"""Load parsed Excel packages into PostgreSQL via AIM ``RDSRepository`` (asyncpg)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import asyncpg

from app.adapters.data_sources.AIM_rds import RDSRepository
from app.core.ingestion.excel.import_context import ImportGeography
from app.core.ingestion.excel.mappers import (
    assign_sequential_sort_orders,
    build_document_rule_condition,
    build_lookup_index,
    build_question_validation_rules,
    build_visibility_condition,
    case_type_code,
    knowledge_content_type,
    knowledge_jurisdiction_id,
    map_question_type,
    matter_type_id,
    normalize_status,
    parse_version_int,
    subtype_code_from_variant,
    subtype_name_from_variant,
    workflow_excel_metadata,
)
from app.core.ingestion.excel.schemas import ExcelPackage
from app.core.ingestion.package_s3 import PackageS3UploadResult


@dataclass
class ImportResult:
    package_import_id: UUID | None = None
    counts: dict[str, int] = field(default_factory=dict)
    id_maps: dict[str, dict[str, str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _jsonb(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, default=str)


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return value


def _read_text_asset(path: Path) -> str | None:
    if path.suffix.lower() not in {".txt", ".md", ".csv", ".json", ".html", ".htm"}:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="latin-1")
        except OSError:
            return None
    except OSError:
        return None


class LegalPackageRDSImporter:
    """Transactional importer — uses AIM ``RDSRepository`` (asyncpg), not SQLAlchemy."""

    def __init__(
        self,
        rds: RDSRepository,
        geography: ImportGeography | None = None,
        bundle_root: Path | None = None,
        s3_assets: PackageS3UploadResult | None = None,
    ) -> None:
        self.rds = rds
        self.geography = geography or ImportGeography.texas_travis_default()
        self.bundle_root = bundle_root
        self.s3_assets = s3_assets or PackageS3UploadResult()
        self._conn: asyncpg.Connection | None = None

    async def import_package(self, package: ExcelPackage) -> ImportResult:
        async with self.rds.transaction() as conn:
            self._conn = conn
            try:
                return await self._import_package(package)
            finally:
                self._conn = None

    async def _import_package(self, package: ExcelPackage) -> ImportResult:
        result = ImportResult()
        result.warnings.extend(package.parse_warnings)
        result.warnings.extend(self.s3_assets.warnings)
        result.counts["s3_uploads"] = self.s3_assets.uploaded

        lookup_index = build_lookup_index(package.lookup_values)
        pkg = package.package

        snapshot_id = await self._conn.fetchval(
            """
            INSERT INTO configuration.package_imports
                (package_id, package_version, source_path, payload)
            VALUES ($1, $2, $3, $4::jsonb)
            RETURNING id
            """,
            pkg.package_id,
            str(pkg.version) if pkg.version else None,
            package.source_path,
            _jsonb(
                {
                    **package.model_dump(mode="json"),
                    "s3_manifest": self.s3_assets.manifest(),
                }
            ),
        )
        result.package_import_id = snapshot_id
        result.counts["s3_documents"] = len(self.s3_assets.manifest()["documents"])
        result.counts["s3_knowledge"] = len(self.s3_assets.manifest()["knowledge"])
        result.counts["s3_assets"] = len(self.s3_assets.manifest()["assets"])

        state = await self._ensure_state()
        county = await self._ensure_county(state.id)
        jurisdiction = await self._ensure_jurisdiction(county.id)
        case_type = await self._ensure_case_type(pkg.practice_area)

        question_ids: dict[str, UUID] = {}
        for row in package.questions:
            q_id = await self._upsert_question(row, lookup_index, pkg.package_id)
            question_ids[row.question_code] = q_id
        result.counts["questions"] = len(question_ids)
        result.id_maps["questions"] = {k: str(v) for k, v in question_ids.items()}

        workflow_ids: dict[str, UUID] = {}
        for wf_row in package.workflows:
            subtype = await self._ensure_case_subtype(
                case_type.id, wf_row, pkg.practice_area
            )
            wf_id = await self._upsert_workflow(
                wf_row, jurisdiction.id, subtype.id, pkg.package_id, package.local_overrides
            )
            workflow_ids[wf_row.workflow_code] = wf_id
        result.counts["workflows"] = len(workflow_ids)
        result.id_maps["workflows"] = {k: str(v) for k, v in workflow_ids.items()}

        mapping_count = 0
        sort_orders = assign_sequential_sort_orders(package.workflow_mappings)
        for m in package.workflow_mappings:
            wf_id = workflow_ids.get(m.workflow_code)
            q_id = question_ids.get(m.question_code)
            if not wf_id or not q_id:
                result.warnings.append(
                    f"Skip mapping {m.workflow_code}/{m.question_code}: unresolved FK"
                )
                continue
            seq = sort_orders.get((m.workflow_code, m.question_code))
            await self._upsert_workflow_question(
                wf_id, q_id, m, seq[0] if seq else m.display_order
            )
            mapping_count += 1
        result.counts["workflow_mappings"] = mapping_count

        doc_count = 0
        for doc in package.documents:
            wf_id = workflow_ids.get(doc.workflow_code)
            if not wf_id:
                result.warnings.append(
                    f"Skip document {doc.document_code}: unknown workflow"
                )
                continue
            await self._upsert_document_chain(wf_id, doc)
            doc_count += 1
        result.counts["documents"] = doc_count

        kb_count = await self._import_knowledge(
            package, pkg.practice_area, pkg.jurisdiction
        )
        result.counts["knowledge"] = kb_count

        result.counts["lookup_values"] = len(package.lookup_values)
        result.counts["local_overrides"] = len(package.local_overrides)

        return result

    async def _ensure_state(self) -> SimpleNamespace:
        g = self.geography
        row = await self._conn.fetchrow(
            "SELECT id FROM configuration.states WHERE code = $1",
            g.state_code,
        )
        if row is None:
            row = await self._conn.fetchrow(
                """
                INSERT INTO configuration.states (code, name, is_active)
                VALUES ($1, $2, TRUE)
                RETURNING id
                """,
                g.state_code,
                g.state_name,
            )
        return SimpleNamespace(id=row["id"])

    async def _ensure_county(self, state_id: UUID) -> SimpleNamespace:
        g = self.geography
        row = await self._conn.fetchrow(
            """
            SELECT id FROM configuration.counties
            WHERE state_id = $1 AND name = $2
            """,
            state_id,
            g.county_name,
        )
        if row is None:
            row = await self._conn.fetchrow(
                """
                INSERT INTO configuration.counties (state_id, code, name, is_active)
                VALUES ($1, $2, $3, TRUE)
                RETURNING id
                """,
                state_id,
                g.county_code,
                g.county_name,
            )
        return SimpleNamespace(id=row["id"])

    async def _ensure_jurisdiction(self, county_id: UUID) -> SimpleNamespace:
        g = self.geography
        row = await self._conn.fetchrow(
            "SELECT id FROM configuration.jurisdictions WHERE code = $1",
            g.jurisdiction_code,
        )
        if row is None:
            row = await self._conn.fetchrow(
                """
                INSERT INTO configuration.jurisdictions
                    (county_id, jurisdiction_type, code, name, is_active)
                VALUES ($1, $2, $3, $4, TRUE)
                RETURNING id
                """,
                county_id,
                g.jurisdiction_type,
                g.jurisdiction_code,
                g.jurisdiction_name,
            )
        return SimpleNamespace(id=row["id"])

    async def _ensure_case_type(self, practice_area: str) -> SimpleNamespace:
        code = case_type_code(practice_area)
        row = await self._conn.fetchrow(
            "SELECT id FROM configuration.case_types WHERE code = $1",
            code,
        )
        if row is None:
            row = await self._conn.fetchrow(
                """
                INSERT INTO configuration.case_types (code, name, is_active)
                VALUES ($1, $2, TRUE)
                RETURNING id
                """,
                code,
                practice_area,
            )
        return SimpleNamespace(id=row["id"])

    async def _ensure_case_subtype(
        self, case_type_id: UUID, wf_row: Any, practice_area: str
    ) -> SimpleNamespace:
        code = subtype_code_from_variant(wf_row.case_variant, practice_area)
        row = await self._conn.fetchrow(
            "SELECT id FROM configuration.case_subtypes WHERE code = $1",
            code,
        )
        if row is None:
            row = await self._conn.fetchrow(
                """
                INSERT INTO configuration.case_subtypes
                    (case_type_id, code, name, description, is_active)
                VALUES ($1, $2, $3, $4, TRUE)
                RETURNING id
                """,
                case_type_id,
                code,
                subtype_name_from_variant(wf_row.case_variant, practice_area),
                wf_row.description,
            )
        return SimpleNamespace(id=row["id"])

    async def _upsert_question(
        self, row: Any, lookup_index: dict, package_id: str
    ) -> UUID:
        existing = await self._conn.fetchrow(
            "SELECT id FROM configuration.questions WHERE code = $1",
            row.question_code,
        )
        validation_rules = build_question_validation_rules(row, lookup_index, package_id)
        question_text = row.question
        question_type = map_question_type(row.input_type)
        placeholder = row.placeholder
        help_text = row.help_text
        is_active = normalize_status(row.status) == "ACTIVE"
        rules_json = _jsonb(validation_rules)

        if existing is None:
            qid = await self._conn.fetchval(
                """
                INSERT INTO configuration.questions
                    (code, question_text, question_type, placeholder, help_text,
                     validation_rules, is_active)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                RETURNING id
                """,
                row.question_code,
                question_text,
                question_type,
                placeholder,
                help_text,
                rules_json,
                is_active,
            )
            return qid

        await self._conn.execute(
            """
            UPDATE configuration.questions
            SET question_text = $2,
                question_type = $3,
                placeholder = $4,
                help_text = $5,
                validation_rules = $6::jsonb,
                is_active = $7,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $1
            """,
            existing["id"],
            question_text,
            question_type,
            placeholder,
            help_text,
            rules_json,
            is_active,
        )
        return existing["id"]

    async def _upsert_workflow(
        self,
        row: Any,
        jurisdiction_id: UUID,
        subtype_id: UUID,
        package_id: str,
        local_overrides: list,
    ) -> UUID:
        existing = await self._conn.fetchrow(
            """
            SELECT id FROM configuration.workflow_definitions
            WHERE workflow_code = $1
              AND jurisdiction_id = $2
              AND case_subtype_id = $3
            """,
            row.workflow_code,
            jurisdiction_id,
            subtype_id,
        )

        meta = workflow_excel_metadata(row, package_id)
        overrides_for_wf = [
            o.model_dump(mode="json")
            for o in local_overrides
            if o.workflow_code == row.workflow_code
        ]
        if overrides_for_wf:
            meta["local_overrides"] = overrides_for_wf

        fields = (
            jurisdiction_id,
            subtype_id,
            row.workflow_name,
            row.workflow_code,
            _jsonb(meta),
            parse_version_int(row.version),
            normalize_status(row.status),
            _as_date(row.effective_from),
            _as_date(row.effective_to),
        )

        if existing is None:
            return await self._conn.fetchval(
                """
                INSERT INTO configuration.workflow_definitions
                    (jurisdiction_id, case_subtype_id, workflow_name, workflow_code,
                     excel_metadata, version, status, effective_from, effective_to)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
                RETURNING id
                """,
                *fields,
            )

        await self._conn.execute(
            """
            UPDATE configuration.workflow_definitions
            SET jurisdiction_id = $2,
                case_subtype_id = $3,
                workflow_name = $4,
                workflow_code = $5,
                excel_metadata = $6::jsonb,
                version = $7,
                status = $8,
                effective_from = $9,
                effective_to = $10,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $1
            """,
            existing["id"],
            *fields,
        )
        return existing["id"]

    async def _upsert_workflow_question(
        self, workflow_id: UUID, question_id: UUID, m: Any, sort_order: int
    ) -> None:
        existing = await self._conn.fetchrow(
            """
            SELECT id FROM configuration.workflow_questions
            WHERE workflow_id = $1 AND question_id = $2
            """,
            workflow_id,
            question_id,
        )
        is_required = bool(m.required) if m.required is not None else True
        visibility = _jsonb(build_visibility_condition(m))
        if existing is None:
            await self._conn.execute(
                """
                INSERT INTO configuration.workflow_questions
                    (workflow_id, question_id, is_required, sort_order, visibility_condition)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                """,
                workflow_id,
                question_id,
                is_required,
                sort_order,
                visibility,
            )
        else:
            await self._conn.execute(
                """
                UPDATE configuration.workflow_questions
                SET is_required = $2,
                    sort_order = $3,
                    visibility_condition = $4::jsonb
                WHERE id = $1
                """,
                existing["id"],
                is_required,
                sort_order,
                visibility,
            )

    async def _upsert_document_chain(self, workflow_id: UUID, doc: Any) -> None:
        template = await self._conn.fetchrow(
            "SELECT id FROM configuration.document_templates WHERE code = $1",
            doc.document_code,
        )
        if template is None:
            template_id = await self._conn.fetchval(
                """
                INSERT INTO configuration.document_templates
                    (code, name, description, is_active)
                VALUES ($1, $2, $3, TRUE)
                RETURNING id
                """,
                doc.document_code,
                doc.document_name,
                doc.output_type,
            )
        else:
            template_id = template["id"]
            await self._conn.execute(
                """
                UPDATE configuration.document_templates
                SET name = $2,
                    description = COALESCE($3, description)
                WHERE id = $1
                """,
                template_id,
                doc.document_name,
                doc.output_type,
            )

        version_num = parse_version_int(doc.template_version or doc.version)
        file_name = doc.file_name or f"{doc.document_code}.ftl"
        s3_ref = self.s3_assets.document_ref(file_name)
        if s3_ref is None:
            raise ValueError(
                f"Document '{doc.document_code}' file '{file_name}' has no S3 upload. "
                "Put the file under ZIP documents/ and set BUCKET_NAME."
            )
        s3_bucket = s3_ref.s3_bucket
        s3_key = s3_ref.s3_key

        version = await self._conn.fetchrow(
            """
            SELECT id FROM configuration.template_versions
            WHERE template_id = $1 AND version = $2
            """,
            template_id,
            version_num,
        )
        if version is None:
            await self._conn.execute(
                """
                INSERT INTO configuration.template_versions
                    (template_id, version, s3_bucket, s3_key)
                VALUES ($1, $2, $3, $4)
                """,
                template_id,
                version_num,
                s3_bucket,
                s3_key,
            )
        else:
            await self._conn.execute(
                """
                UPDATE configuration.template_versions
                SET s3_bucket = $2,
                    s3_key = $3
                WHERE id = $1
                """,
                version["id"],
                s3_bucket,
                s3_key,
            )

        rule = await self._conn.fetchrow(
            """
            SELECT id FROM configuration.document_rules
            WHERE workflow_id = $1 AND template_id = $2
            """,
            workflow_id,
            template_id,
        )
        is_required = bool(doc.required) if doc.required is not None else True
        condition = _jsonb(build_document_rule_condition(doc))
        if rule is None:
            await self._conn.execute(
                """
                INSERT INTO configuration.document_rules
                    (workflow_id, template_id, is_required, condition)
                VALUES ($1, $2, $3, $4::jsonb)
                """,
                workflow_id,
                template_id,
                is_required,
                condition,
            )
        else:
            await self._conn.execute(
                """
                UPDATE configuration.document_rules
                SET is_required = $2,
                    condition = $3::jsonb
                WHERE id = $1
                """,
                rule["id"],
                is_required,
                condition,
            )

    async def _import_knowledge(
        self, package: ExcelPackage, practice_area: str, jurisdiction_name: str
    ) -> int:
        mt_id = matter_type_id(practice_area)
        kj_id = knowledge_jurisdiction_id(jurisdiction_name)

        mt = await self._conn.fetchrow(
            "SELECT matter_type_id FROM knowledge.matter_types WHERE matter_type_id = $1",
            mt_id,
        )
        if mt is None:
            await self._conn.execute(
                """
                INSERT INTO knowledge.matter_types
                    (matter_type_id, display_name, required_content_types, active)
                VALUES ($1, $2, $3::text[], TRUE)
                """,
                mt_id,
                practice_area,
                ["faq", "court_rule_or_requirement"],
            )

        kj = await self._conn.fetchrow(
            "SELECT jurisdiction_id FROM knowledge.jurisdictions WHERE jurisdiction_id = $1",
            kj_id,
        )
        if kj is None:
            abbr = kj_id.split("_")[-1].upper()
            await self._conn.execute(
                """
                INSERT INTO knowledge.jurisdictions
                    (jurisdiction_id, state, abbreviation, scope, requires_county_configuration)
                VALUES ($1, $2, $3, 'statewide_base', TRUE)
                """,
                kj_id,
                jurisdiction_name,
                abbr,
            )

        count = 0
        for row in package.knowledge:
            content_type = knowledge_content_type(row.category)
            s3_ref = self.s3_assets.knowledge_ref(row.file_name)
            chunk = row.content or row.title or row.knowledge_code
            if not row.content and s3_ref is not None:
                text_body = _read_text_asset(s3_ref.local_path)
                if text_body:
                    chunk = text_body
            topics = [t.strip() for t in (row.tags or "").split(",") if t.strip()] or None
            last_ver = _as_date(row.last_verified)

            kb = await self._conn.fetchrow(
                "SELECT content_id FROM knowledge.kb_vectors WHERE content_id = $1",
                row.knowledge_code,
            )
            if kb is None:
                await self._conn.execute(
                    """
                    INSERT INTO knowledge.kb_vectors
                        (content_id, content_type, matter_type, jurisdiction_id, state,
                         case_variant, topics, text, embedding, embedding_model, last_verified)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::text[], $8, NULL, 'pending', $9)
                    """,
                    row.knowledge_code,
                    content_type,
                    mt_id,
                    kj_id,
                    jurisdiction_name,
                    row.workflow_code,
                    topics,
                    chunk,
                    last_ver,
                )
            else:
                await self._conn.execute(
                    """
                    UPDATE knowledge.kb_vectors
                    SET content_type = $2,
                        matter_type = $3,
                        jurisdiction_id = $4,
                        state = $5,
                        case_variant = $6,
                        topics = $7::text[],
                        text = $8,
                        embedding = NULL,
                        embedding_model = 'pending',
                        last_verified = $9
                    WHERE content_id = $1
                    """,
                    row.knowledge_code,
                    content_type,
                    mt_id,
                    kj_id,
                    jurisdiction_name,
                    row.workflow_code,
                    topics,
                    chunk,
                    last_ver,
                )

            if row.source or row.file_name:
                source_id = f"{row.knowledge_code}_SRC"
                s3_ref = self.s3_assets.knowledge_ref(row.file_name)
                if row.file_name and s3_ref is None:
                    raise ValueError(
                        f"Knowledge '{row.knowledge_code}' file '{row.file_name}' has no S3 upload. "
                        "Put the file under ZIP knowledge/ and set BUCKET_NAME."
                    )
                source_url = s3_ref.s3_uri if s3_ref is not None else str(row.source)
                src = await self._conn.fetchrow(
                    "SELECT source_id FROM knowledge.sources WHERE source_id = $1",
                    source_id,
                )
                if src is None:
                    await self._conn.execute(
                        """
                        INSERT INTO knowledge.sources
                            (source_id, title, url, authority, last_verified)
                        VALUES ($1, $2, $3, 'secondary', $4)
                        """,
                        source_id,
                        row.title or row.knowledge_code,
                        source_url,
                        last_ver or date.today(),
                    )
                else:
                    await self._conn.execute(
                        """
                        UPDATE knowledge.sources
                        SET title = $2,
                            url = $3,
                            last_verified = COALESCE($4, last_verified)
                        WHERE source_id = $1
                        """,
                        source_id,
                        row.title or row.knowledge_code,
                        source_url,
                        last_ver,
                    )
                link = await self._conn.fetchrow(
                    """
                    SELECT content_id FROM knowledge.vector_sources
                    WHERE content_id = $1 AND source_id = $2
                    """,
                    row.knowledge_code,
                    source_id,
                )
                if link is None:
                    await self._conn.execute(
                        """
                        INSERT INTO knowledge.vector_sources (content_id, source_id)
                        VALUES ($1, $2)
                        """,
                        row.knowledge_code,
                        source_id,
                    )
            count += 1
        return count
