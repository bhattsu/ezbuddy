"""Discover and safely extract legal configuration package ZIP archives."""

from __future__ import annotations

import logging
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)

EXCEL_SUFFIX = ".xlsx"
IGNORE_EXCEL_PREFIX = "~$"


@dataclass(frozen=True)
class LegalPackageArchiveLayout:
    """Resolved layout after extracting a client ZIP upload."""

    root_dir: Path
    excel_path: Path
    documents_dir: Path | None
    knowledge_dir: Path | None
    assets_dir: Path | None
    readme_path: Path | None
    archive_filename: str
    excel_relative_path: str

    def manifest(self) -> dict[str, str | None]:
        return {
            "archive_filename": self.archive_filename,
            "excel": self.excel_relative_path,
            "documents_dir": _rel_static(self.root_dir, self.documents_dir),
            "knowledge_dir": _rel_static(self.root_dir, self.knowledge_dir),
            "assets_dir": _rel_static(self.root_dir, self.assets_dir),
            "readme": _rel_static(self.root_dir, self.readme_path),
        }


def _rel_static(root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _is_safe_zip_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or ".." in Path(normalized).parts:
        return False
    return True


def extract_zip_to_temp(zip_bytes: bytes, archive_filename: str) -> Path:
    """Extract ZIP to a new temp directory; raises ValueError on unsafe paths."""
    temp_dir = Path(tempfile.mkdtemp(prefix="legal_pkg_"))
    try:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if not _is_safe_zip_member(info.filename):
                    raise ValueError(f"Unsafe path in zip: {info.filename!r}")
            zf.extractall(temp_dir)
        return temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def discover_package_layout(extract_root: Path, archive_filename: str) -> LegalPackageArchiveLayout:
    """
    Find the package root (handles nested Package folders) and main workbook.
    """
    package_root = _find_package_root(extract_root)
    excel_path = _find_workbook(package_root)
    if excel_path is None:
        raise ValueError(
            "No configuration workbook found (.xlsx). Expected a file like Texas_Divorce.xlsx "
            "at the package root (Excel temp files ~$*.xlsx are ignored)."
        )
    package_root = excel_path.parent

    documents_dir = _first_existing_dir(package_root, "documents")
    knowledge_dir = _first_existing_dir(package_root, "knowledge")
    assets_dir = _first_existing_dir(package_root, "assets")
    readme_path = _first_existing_file(package_root, "README.txt", "readme.txt")

    return LegalPackageArchiveLayout(
        root_dir=package_root,
        excel_path=excel_path,
        documents_dir=documents_dir,
        knowledge_dir=knowledge_dir,
        assets_dir=assets_dir,
        readme_path=readme_path,
        archive_filename=archive_filename,
        excel_relative_path=str(excel_path.relative_to(package_root)).replace("\\", "/"),
    )


def _find_package_root(extract_root: Path) -> Path:
    """Prefer inner folder that contains an .xlsx workbook."""
    candidates: list[Path] = [extract_root]
    for child in extract_root.iterdir():
        if child.is_dir():
            candidates.append(child)
            nested = child / child.name
            if nested.is_dir():
                candidates.append(nested)

    for root in candidates:
        if _find_workbook(root) is not None:
            return root

    if list(extract_root.rglob(f"*{EXCEL_SUFFIX}")):
        for path in extract_root.rglob(f"*{EXCEL_SUFFIX}"):
            if not path.name.startswith(IGNORE_EXCEL_PREFIX):
                return path.parent
    return extract_root


def _find_workbook(root: Path) -> Path | None:
    workbooks = [
        p
        for p in root.glob("*.xlsx")
        if p.is_file() and not p.name.startswith(IGNORE_EXCEL_PREFIX)
    ]
    if not workbooks:
        workbooks = [
            p
            for p in root.rglob("*.xlsx")
            if p.is_file() and not p.name.startswith(IGNORE_EXCEL_PREFIX)
        ]
    if not workbooks:
        return None
    if len(workbooks) == 1:
        return workbooks[0]
    workbooks.sort(key=lambda p: (len(str(p)), p.name))
    logger.warning("Multiple workbooks in package; using %s", workbooks[0].name)
    return workbooks[0]


def _first_existing_dir(root: Path, name: str) -> Path | None:
    direct = root / name
    if direct.is_dir():
        return direct
    for hit in root.rglob(name):
        if hit.is_dir() and hit.name == name:
            return hit
    return None


def _first_existing_file(root: Path, *names: str) -> Path | None:
    for name in names:
        direct = root / name
        if direct.is_file():
            return direct
    for name in names:
        for hit in root.rglob(name):
            if hit.is_file():
                return hit
    return None


def cleanup_extract_dir(path: Path | None) -> None:
    if path is None:
        return
    shutil.rmtree(path, ignore_errors=True)
