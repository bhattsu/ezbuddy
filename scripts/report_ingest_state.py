"""
Post-ingest report: DB sort_order, S3 keys, latest package snapshot.

Usage:
  PYTHONPATH=. python scripts/report_ingest_state.py
  PYTHONPATH=. python scripts/report_ingest_state.py --out ingest_report.txt
  PYTHONPATH=. python scripts/report_ingest_state.py --workflow TX_DIV_CHILD
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from app.config.settings import settings
from app.services.aim_factory import make_rds_repo
from app.utils.s3_utils import S3Manager


def _lines(title: str, rows: list[dict]) -> list[str]:
    out = [f"\n=== {title} ==="]
    if not rows:
        out.append("(none)")
        return out
    for row in rows:
        out.append(json.dumps(row, default=str, ensure_ascii=False))
    return out


async def _s3_list(prefix: str, max_keys: int = 50) -> list[str]:
    out: list[str] = []
    bucket = (settings.BUCKET_NAME or "").strip()
    if not bucket:
        return ["S3: BUCKET_NAME not set — skip listing"]
    try:
        manager = S3Manager()
        objects = await manager.list_objects(prefix, bucket=bucket, max_keys=max_keys)
        for obj in objects:
            key = str(obj["key"])
            size = obj.get("size", 0)
            modified = obj.get("last_modified")
            out.append(f"s3://{bucket}/{key}  ({size} bytes, {modified})")
        if len(objects) >= max_keys:
            out.append(f"... truncated after {max_keys} keys under {prefix!r}")
        if not out:
            out.append(f"(no objects under s3://{bucket}/{prefix})")
    except Exception as exc:  # noqa: BLE001
        out.append(f"S3 list failed: {exc}")
    return out


async def build_report(workflow_filter: str | None) -> str:
    repo = make_rds_repo()
    if repo is None:
        return "RDS not configured (set RDS_* in .env)"

    lines: list[str] = [
        f"Ingest state report — {datetime.utcnow().isoformat()}Z",
        f"RDS: {settings.rds_host}:{settings.rds_port}/{settings.rds_database}",
        f"S3 bucket: {settings.BUCKET_NAME or '(unset)'}",
        f"S3 documents prefix: {settings.S3_DOCUMENTS_REPO_PREFIX}",
        f"S3 knowledge prefix: {settings.S3_KNOWLEDGE_PREFIX}",
    ]

    try:
        latest = await repo.fetch(
            """
            SELECT id, package_id, package_version, source_path, imported_at
            FROM configuration.package_imports
            ORDER BY imported_at DESC NULLS LAST, id DESC
            LIMIT 3
            """
        )
        lines.extend(_lines("Latest package_imports (audit snapshots)", latest))

        if workflow_filter:
            sort_rows = await repo.fetch(
                """
                SELECT
                    wd.workflow_code,
                    q.code AS question_code,
                    wq.sort_order,
                    wq.visibility_condition->'_excel'->>'display_order' AS excel_display_order,
                    wq.is_required
                FROM configuration.workflow_questions wq
                JOIN configuration.workflow_definitions wd ON wd.id = wq.workflow_id
                JOIN configuration.questions q ON q.id = wq.question_id
                WHERE wd.workflow_code = $1
                ORDER BY wq.sort_order, q.code
                """,
                workflow_filter,
            )
        else:
            sort_rows = await repo.fetch(
                """
                SELECT
                    wd.workflow_code,
                    q.code AS question_code,
                    wq.sort_order,
                    wq.visibility_condition->'_excel'->>'display_order' AS excel_display_order,
                    wq.is_required
                FROM configuration.workflow_questions wq
                JOIN configuration.workflow_definitions wd ON wd.id = wq.workflow_id
                JOIN configuration.questions q ON q.id = wq.question_id
                WHERE wd.workflow_code LIKE 'TX_DIV%'
                ORDER BY wd.workflow_code, wq.sort_order, q.code
                """
            )

        lines.extend(
            _lines(
                "Workflow question order (DB sort_order vs Excel display_order)",
                sort_rows,
            )
        )

        # Highlight shared excel display_order with different sort_order
        by_wf_excel: dict[tuple[str, str], list] = {}
        for row in sort_rows:
            key = (row["workflow_code"], str(row.get("excel_display_order")))
            by_wf_excel.setdefault(key, []).append(row)
        fixes = []
        for (wf, excel_ord), group in sorted(by_wf_excel.items()):
            if len(group) > 1 and excel_ord not in ("None", "null", ""):
                sorts = [g["sort_order"] for g in group]
                if len(set(sorts)) == len(sorts):
                    fixes.append(
                        {
                            "workflow": wf,
                            "excel_display_order": excel_ord,
                            "note": "shared Excel order -> unique DB sort_order",
                            "questions": [
                                {
                                    "question": g["question_code"],
                                    "sort_order": g["sort_order"],
                                }
                                for g in group
                            ],
                        }
                    )
        if fixes:
            lines.extend(_lines("Sequential sort_order fix applied (examples)", fixes))

        templates = await repo.fetch(
            """
            SELECT
                dt.code AS template_code,
                tv.version,
                tv.s3_bucket,
                tv.s3_key
            FROM configuration.template_versions tv
            JOIN configuration.document_templates dt ON dt.id = tv.template_id
            ORDER BY dt.code, tv.version
            """
        )
        lines.extend(_lines("Document templates (S3 in RDS)", templates))

        knowledge = await repo.fetch(
            """
            SELECT
                kv.content_id AS knowledge_code,
                kv.content_type,
                kv.state,
                kv.case_variant,
                LEFT(kv.text, 120) AS chunk_preview,
                s.url AS source_url
            FROM knowledge.kb_vectors kv
            LEFT JOIN knowledge.vector_sources vs ON vs.content_id = kv.content_id
            LEFT JOIN knowledge.sources s ON s.source_id = vs.source_id
            ORDER BY kv.content_id
            LIMIT 20
            """
        )
        lines.extend(_lines("Knowledge rows (preview)", knowledge))

        lines.append("\n=== S3 objects (documents prefix) ===")
        lines.extend(await _s3_list(settings.S3_DOCUMENTS_REPO_PREFIX.strip("/") + "/"))

        lines.append("\n=== S3 objects (knowledge prefix) ===")
        lines.extend(await _s3_list(settings.S3_KNOWLEDGE_PREFIX.strip("/") + "/"))

        # Also list legacy top-level folders if present
        lines.append("\n=== S3 objects (documents-repo/ legacy) ===")
        lines.extend(await _s3_list("documents-repo/"))

        lines.append("\n=== S3 objects (knowledge/ top-level) ===")
        lines.extend(await _s3_list("knowledge/"))

    finally:
        await repo.close()

    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Report DB + S3 state after package ingest")
    parser.add_argument(
        "--out",
        default="",
        help="Write report to this file (default: stdout only)",
    )
    parser.add_argument(
        "--workflow",
        default="",
        help="Filter sort_order section to one workflow_code (e.g. TX_DIV_CHILD)",
    )
    args = parser.parse_args()

    text = await build_report(args.workflow or None)
    print(text)
    if args.out:
        path = Path(args.out)
        path.write_text(text, encoding="utf-8")
        print(f"\nWrote {path.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
