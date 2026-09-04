"""Upload legal package ZIP assets (documents / knowledge / assets) to S3."""

from __future__ import annotations

import logging
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

from app.config.settings import settings
from app.core.ingestion.excel.schemas import ExcelPackage
from app.core.ingestion.package_archive import LegalPackageArchiveLayout
from app.utils.s3_utils import S3Manager

logger = logging.getLogger(__name__)


@dataclass
class PackageAssetRef:
    """Uploaded object location for a package file."""

    local_path: Path
    s3_bucket: str
    s3_key: str
    kind: str  # documents | knowledge | assets

    @property
    def s3_uri(self) -> str:
        return f"s3://{self.s3_bucket}/{self.s3_key}"

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "local_name": self.local_path.name,
            "s3_bucket": self.s3_bucket,
            "s3_key": self.s3_key,
            "s3_uri": self.s3_uri,
        }


@dataclass
class PackageS3UploadResult:
    """Maps file names / relative paths to uploaded S3 objects."""

    documents_by_file: dict[str, PackageAssetRef] = field(default_factory=dict)
    knowledge_by_file: dict[str, PackageAssetRef] = field(default_factory=dict)
    assets_by_file: dict[str, PackageAssetRef] = field(default_factory=dict)
    all_uploads: list[PackageAssetRef] = field(default_factory=list)
    uploaded: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def document_ref(self, file_name: str | None) -> PackageAssetRef | None:
        return self._lookup(self.documents_by_file, file_name)

    def knowledge_ref(self, file_name: str | None) -> PackageAssetRef | None:
        return self._lookup(self.knowledge_by_file, file_name)

    def manifest(self) -> dict[str, list[dict[str, str]]]:
        return {
            "documents": [r.as_dict() for r in self._unique(self.documents_by_file)],
            "knowledge": [r.as_dict() for r in self._unique(self.knowledge_by_file)],
            "assets": [r.as_dict() for r in self._unique(self.assets_by_file)],
        }

    @staticmethod
    def _lookup(mapping: dict[str, PackageAssetRef], file_name: str | None) -> PackageAssetRef | None:
        if not file_name:
            return None
        key = _norm_name(file_name)
        if key in mapping:
            return mapping[key]
        # also try basename only
        return mapping.get(_norm_name(Path(file_name.replace("\\", "/")).name))

    @staticmethod
    def _unique(mapping: dict[str, PackageAssetRef]) -> list[PackageAssetRef]:
        seen: set[str] = set()
        out: list[PackageAssetRef] = []
        for ref in mapping.values():
            if ref.s3_key in seen:
                continue
            seen.add(ref.s3_key)
            out.append(ref)
        return out


def _norm_name(name: str) -> str:
    return name.replace("\\", "/").strip().lower().lstrip("./")


def _guess_content_type(path: Path) -> str | None:
    ctype, _ = mimetypes.guess_type(str(path))
    return ctype


def _iter_files(directory: Path | None) -> list[Path]:
    if directory is None or not directory.is_dir():
        return []
    files: list[Path] = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not path.name.startswith(".") and not path.name.startswith("~$"):
            files.append(path)
    return files


def _resolve_local_file(directory: Path | None, file_name: str | None) -> Path | None:
    if directory is None or not file_name:
        return None
    rel = file_name.replace("\\", "/").lstrip("./")
    candidate = directory / rel
    if candidate.is_file():
        return candidate
    # basename anywhere under directory
    wanted = Path(rel).name.lower()
    for path in _iter_files(directory):
        if path.name.lower() == wanted:
            return path
    return None


def _index_ref(
    mapping: dict[str, PackageAssetRef],
    ref: PackageAssetRef,
    *aliases: str,
) -> None:
    mapping[_norm_name(ref.local_path.name)] = ref
    for alias in aliases:
        if alias:
            mapping[_norm_name(alias)] = ref
            mapping[_norm_name(Path(alias.replace("\\", "/")).name)] = ref


async def _upload_one(
    manager: S3Manager,
    *,
    local: Path,
    bucket: str,
    s3_key: str,
    kind: str,
) -> PackageAssetRef:
    await manager.upload_file_at_key(
        str(local),
        s3_key,
        bucket=bucket,
        content_type=_guess_content_type(local),
    )
    return PackageAssetRef(
        local_path=local,
        s3_bucket=bucket,
        s3_key=s3_key,
        kind=kind,
    )


async def upload_package_assets(
    layout: LegalPackageArchiveLayout,
    package: ExcelPackage,
    *,
    s3: S3Manager | None = None,
    strict: bool = False,
) -> PackageS3UploadResult:
    """
    Upload every file under ZIP documents/, knowledge/, assets/ into BUCKET_NAME,
    then ensure Excel-referenced names resolve to those uploads.

    Keys (flat under BUCKET_NAME — see S3_*_PREFIX in .env):
      documents-repo/{relative_path}
      knowledge/{relative_path}
    """
    result = PackageS3UploadResult()
    bucket = (settings.BUCKET_NAME or "").strip()
    if not bucket:
        msg = (
            "BUCKET_NAME not set — package files were not uploaded to S3 "
            "(DB would get placeholder keys)."
        )
        result.warnings.append(msg)
        if strict:
            result.errors.append(msg)
        return result

    manager = s3 or S3Manager()
    docs_prefix = settings.S3_DOCUMENTS_REPO_PREFIX.strip("/") or "documents-repo"
    knowledge_prefix = settings.S3_KNOWLEDGE_PREFIX.strip("/") or "knowledge"
    assets_prefix = settings.S3_PACKAGE_ASSETS_PREFIX.strip("/") or docs_prefix

    def _object_key(prefix: str, rel: str) -> str:
        return f"{prefix.strip('/')}/{rel.replace(chr(92), '/').lstrip('./')}"

    # 1) Upload ALL files present in the ZIP folders (full sync)
    for local in _iter_files(layout.documents_dir):
        rel = local.relative_to(layout.documents_dir).as_posix()
        s3_key = _object_key(docs_prefix, rel)
        try:
            ref = await _upload_one(
                manager, local=local, bucket=bucket, s3_key=s3_key, kind="documents"
            )
            _index_ref(result.documents_by_file, ref, rel, local.name)
            result.all_uploads.append(ref)
            result.uploaded += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"S3 upload failed for document {rel}: {exc}"
            result.warnings.append(msg)
            result.errors.append(msg)

    for local in _iter_files(layout.knowledge_dir):
        rel = local.relative_to(layout.knowledge_dir).as_posix()
        s3_key = _object_key(knowledge_prefix, rel)
        try:
            ref = await _upload_one(
                manager, local=local, bucket=bucket, s3_key=s3_key, kind="knowledge"
            )
            _index_ref(result.knowledge_by_file, ref, rel, local.name)
            result.all_uploads.append(ref)
            result.uploaded += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"S3 upload failed for knowledge {rel}: {exc}"
            result.warnings.append(msg)
            result.errors.append(msg)

    for local in _iter_files(layout.assets_dir):
        rel = local.relative_to(layout.assets_dir).as_posix()
        s3_key = _object_key(assets_prefix, f"assets/{rel}")
        try:
            ref = await _upload_one(
                manager, local=local, bucket=bucket, s3_key=s3_key, kind="assets"
            )
            _index_ref(result.assets_by_file, ref, rel, local.name)
            result.all_uploads.append(ref)
            result.uploaded += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"S3 upload failed for asset {rel}: {exc}"
            result.warnings.append(msg)
            result.errors.append(msg)

    # 2) Validate Excel references resolve to uploaded objects
    for doc in package.documents:
        file_name = doc.file_name or f"{doc.document_code}.ftl"
        if result.document_ref(file_name) is not None:
            continue
        local = _resolve_local_file(layout.documents_dir, file_name)
        if local is None:
            msg = (
                f"Document file missing in ZIP documents/: {file_name} "
                f"(document_code={doc.document_code})"
            )
            result.warnings.append(msg)
            result.errors.append(msg)
            continue
        # File existed but wasn't uploaded (should be rare after full folder upload)
        rel = local.relative_to(layout.documents_dir).as_posix()
        s3_key = _object_key(docs_prefix, rel)
        try:
            ref = await _upload_one(
                manager, local=local, bucket=bucket, s3_key=s3_key, kind="documents"
            )
            _index_ref(result.documents_by_file, ref, file_name, rel, local.name)
            result.all_uploads.append(ref)
            result.uploaded += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"S3 upload failed for document {file_name}: {exc}"
            result.warnings.append(msg)
            result.errors.append(msg)

    for row in package.knowledge:
        if not row.file_name:
            continue
        if result.knowledge_ref(row.file_name) is not None:
            continue
        local = _resolve_local_file(layout.knowledge_dir, row.file_name)
        if local is None:
            msg = (
                f"Knowledge file missing in ZIP knowledge/: {row.file_name} "
                f"(knowledge_code={row.knowledge_code})"
            )
            result.warnings.append(msg)
            result.errors.append(msg)
            continue
        rel = local.relative_to(layout.knowledge_dir).as_posix()
        s3_key = _object_key(knowledge_prefix, rel)
        try:
            ref = await _upload_one(
                manager, local=local, bucket=bucket, s3_key=s3_key, kind="knowledge"
            )
            _index_ref(result.knowledge_by_file, ref, row.file_name, rel, local.name)
            result.all_uploads.append(ref)
            result.uploaded += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"S3 upload failed for knowledge {row.file_name}: {exc}"
            result.warnings.append(msg)
            result.errors.append(msg)

    logger.info(
        "Package S3 upload complete: package_id=%s uploaded=%s warnings=%s errors=%s",
        package.package.package_id,
        result.uploaded,
        len(result.warnings),
        len(result.errors),
    )
    return result
