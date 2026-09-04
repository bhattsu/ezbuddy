# Excel → RDS ingestion pipeline

## Flow

```text
Texas_Divorce.xlsx
    → LegalPackageExcelParser (typed ExcelPackage)
    → LegalPackageRDSImporter (single DB transaction)
    → configuration.* + knowledge.*
```

## Run

```bash
# Apply migration 0002 first (workflow_code, package_imports snapshot table)
alembic upgrade head

python -m app.core.ingestion.pipeline "path/to/Texas_Divorce.xlsx"
```

## Data preservation

| Excel data | RDS destination |
|------------|-----------------|
| Full workbook | `configuration.package_imports.payload` (JSONB snapshot) |
| Questions + Lookup Lists | `configuration.questions.validation_rules` (`options`, `lookup_code`, tags, etc.) |
| Workflow Mapping extras | `workflow_questions.visibility_condition._excel` |
| Placeholder mapping | `document_rules.condition.placeholder_mapping` |
| Workflow codes | `workflow_definitions.workflow_code` + `excel_metadata` |
| Local Overrides | `excel_metadata.local_overrides` on matching workflow |
| Knowledge | `knowledge.kb_vectors` (+ `sources`, `vector_sources`); embedding `pending` |

## Geography

Workflows attach to UUID configuration graph via `ImportGeography` (default: TX / Travis / district court). Override when importing other states:

```python
import asyncio
from app.core.ingestion.excel.import_context import ImportGeography
from app.core.ingestion.pipeline import LegalPackageIngestionPipeline
from app.services.aim_factory import make_rds_repo

geo = ImportGeography(
    state_code="TX",
    state_name="Texas",
    county_name="Harris",
    county_code="HARRIS",
    jurisdiction_code="TX-HARRIS-DISTRICT",
    jurisdiction_name="Harris County District Court",
)

async def main():
    rds = make_rds_repo()
    try:
        await LegalPackageIngestionPipeline(geography=geo).run("package.xlsx", rds=rds)
    finally:
        await rds.close()

asyncio.run(main())
```

## Connectivity

- **Reads / schema checks / Excel import writes:** shared `RDSRepository` pool (`aim_factory` + AIM `execute` / `transaction`).
- **ZIP document/knowledge/assets files:** uploaded to `BUCKET_NAME` under `S3_DOCUMENTS_REPO_PREFIX` / `S3_KNOWLEDGE_PREFIX` (see `package_s3.py`); paths stored on `template_versions` and `knowledge.sources`.
- **Local bootstrap / seeds only:** `app.core.db.sync_ingestion` (ORM `create_all` / sample data).
